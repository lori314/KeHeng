"""Run one Estun/hash technology validation against archived actual context.

Secrets must be supplied via KEHENG_LLM_* environment variables. The command
creates a new timestamped directory and hard-limits provider HTTP retries to 0
and format repairs to 1 (maximum 2 generation requests total).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.technology_agent import TechnologyAgent
from app.core.config import get_settings
from app.llm import LLMContextChunk, LLMExtractionRequest, OpenAICompatibleProvider
from app.llm.api_model import OpenAICompatibleHTTPTransport
from app.llm.provider import LLMProviderError
from app.rag.retrieval import RetrievedEvidence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "runtime/review/paired_model/run-20260928T110017Z-2bc83078/paired_model_results.json"
DEFAULT_FROZEN = ROOT / "runtime/review/paired_model/run-20260928T110017Z-2bc83078/iteration04_frozen_config.json"
PROMPT_PATH = ROOT / "prompts/technology_agent_prompt.md"
PROMPT_VERSION = "iteration06-summary-refusal-contract-v1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_output_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / f"estun-hash-tech-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    target.mkdir()  # fails on collision; never reuses or overwrites a run
    return target


async def run(source_path: Path, frozen_path: Path, output_parent: Path) -> Path:
    settings = get_settings()
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        raise RuntimeError("missing KEHENG_LLM_ENDPOINT, KEHENG_LLM_MODEL, or KEHENG_LLM_API_KEY; no request sent")

    source = json.loads(source_path.read_text(encoding="utf-8"))
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    matches = [item for item in source["results"] if item.get("company_id") == "estun" and item.get("retriever") == "hash"]
    if len(matches) != 1 or matches[0].get("status") != "partial":
        raise RuntimeError("expected exactly one archived Estun/hash partial result")
    old = matches[0]
    archived_requests = old.get("actual_model_requests") or []
    if not archived_requests:
        raise RuntimeError("archived result has no actual model request; refusing context substitution")
    archived_request = archived_requests[0]
    if not archived_request.get("chunks") or not archived_request.get("prompt"):
        raise RuntimeError("archived request context or prompt is incomplete; refusing context substitution")

    # Preserve exact archived chunks/E IDs; only the summary refusal contract prompt changes.
    chunks = [LLMContextChunk.model_validate(chunk) for chunk in archived_request["chunks"]]
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    frozen_source_paths = [
        "backend/app/agents/technology_agent.py",
        "backend/app/llm/provider.py",
        "backend/app/llm/api_model.py",
        "backend/app/core/config.py",
        "backend/app/rag/retrieval.py",
        "backend/app/rag/context_assembly.py",
        "prompts/technology_agent_prompt.md",
        "evaluation/run_targeted_estun_replay.py",
        "tests/test_iteration05_schema_repair.py",
    ]
    source_hashes = {path: sha256((ROOT / path).read_bytes()) for path in frozen_source_paths}
    request = LLMExtractionRequest(
        task_id=archived_request["task_id"],
        enterprise_name=archived_request["enterprise_name"],
        chunks=chunks,
        prompt=prompt,
        context_assembly={
            "strategy": "archived_actual_context_replay",
            "source_result": source_path.name,
            "source_context_request_index": 0,
            "context_identical_to_archived_request": True,
        },
    )

    transport = OpenAICompatibleHTTPTransport(
        settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
        temperature=0.0,
        max_retries=0,
        json_mode=True,
    )
    provider = OpenAICompatibleProvider(
        settings.llm_endpoint,
        settings.llm_model,
        transport=transport,
        repair_enabled=True,
    )
    output_dir = make_output_dir(output_parent)
    (output_dir / "actual_context.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in chunks], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    status = "not_completed"
    contract_dump = None
    semantic_error = None
    provider_error = None
    try:
        result = await provider.extract(request)
        retrieved = [
            RetrievedEvidence(
                task_id=request.task_id,
                document_id=item.chunk_id,
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                text=item.excerpt,
                locator=f"page:{item.page_number}" if item.page_number is not None else "unknown",
                score=item.retrieval_score,
            )
            for item in chunks
        ]
        agent = TechnologyAgent(retriever=None)
        try:
            converted = agent._convert_llm_result(result, retrieved, {item.chunk_id: item.evidence_id for item in chunks})
            contract_dump = converted.model_dump(mode="json")
            status = "accepted_by_schema_and_task_reference_checks"
        except Exception as exc:  # semantic/task-reference post-validation is a separate boundary
            status = "schema_valid_but_agent_contract_rejected"
            semantic_error = {"type": type(exc).__name__, "message": str(exc)}
            contract_dump = result.model_dump(mode="json")
    except LLMProviderError as exc:
        status = "provider_or_schema_failure"
        provider_error = {
            "category": exc.category,
            "status_code": exc.status_code,
            "diagnostics": exc.diagnostics,
            "request_count": transport.call_count,
        }
    finally:
        if transport.call_count > 2:
            raise RuntimeError("hard request budget exceeded")

    output = {
        "schema_version": "iteration06-targeted-replay-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "single Estun/hash technology validation; exploratory, not accuracy evaluation",
        "provider": provider.name,
        "model": settings.llm_model,
        "endpoint_host": settings.llm_endpoint.split("/")[2] if "://" in settings.llm_endpoint else "configured",
        "temperature": 0.0,
        "http_max_retries": 0,
        "format_repairs_max": 1,
        "http_generation_requests": transport.call_count,
        "status": status,
        "provider_error": provider_error,
        "agent_contract_error": semantic_error,
        "source_run_file_sha256": sha256(source_path.read_bytes()),
        "frozen_config_file_sha256": sha256(frozen_path.read_bytes()),
        "prior_source_fingerprint_sha256": frozen.get("source_fingerprint_sha256"),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha256(prompt.encode("utf-8")),
        "previous_prompt_sha256": sha256(archived_request["prompt"].encode("utf-8")),
        "source_file_sha256": source_hashes,
        "source_fingerprint_sha256": sha256(json.dumps(source_hashes, sort_keys=True).encode("utf-8")),
        "actual_context_sha256": sha256(json.dumps([item.model_dump(mode="json") for item in chunks], ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "input_context_is_archived_actual": True,
        "input_context_characters": sum(len(item.excerpt) for item in chunks),
        "input_evidence_ids": [item.evidence_id for item in chunks],
        "raw_http_responses": transport.response_history,
        "raw_model_contents": transport.content_history,
        "request_prompts": [
            {"prompt_sha256": sha256(item["prompt"].encode("utf-8")), "prompt": item["prompt"]}
            for item in transport.request_history
        ],
        "observations": transport.observation_history,
        "agent_result": contract_dump,
        "validation_note": "schema/task citation validation does not establish semantic support; human review is still required",
    }
    (output_dir / "targeted_replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--frozen-config", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--output-parent", type=Path, default=ROOT / "runtime/review/targeted_replay")
    args = parser.parse_args()
    output = asyncio.run(run(args.source, args.frozen_config, args.output_parent))
    print(output)


if __name__ == "__main__":
    main()
