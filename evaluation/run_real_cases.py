"""Run the six provenance-tracked public company cases in real LLM mode.

This is a case-study runner, not a benchmark with fabricated gold labels.  A
failed case is recorded explicitly and is never retried in rule mode.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider  # noqa: E402
from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.run_v09_experiments import (  # noqa: E402
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    PROMPT_VERSION,
    discover_api_key,
)
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402
from evaluation.provenance import build_run_provenance  # noqa: E402
from types import SimpleNamespace


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_human_review(output_root: Path, cases: list[dict[str, Any]]) -> None:
    fields = [
        "company", "system_status", "technology_score", "industry_score", "overall_score",
        "technology_reasoning_summary", "industry_reasoning_summary", "evidence_ids",
        "technology_reasonableness", "industry_reasonableness", "evidence_support",
        "overclaiming", "trl_reasonableness", "notes",
    ]
    rows = []
    for metadata in cases:
        case_dir = output_root / metadata["case_id"]
        tech_path = case_dir / "technology_analysis.json"
        ind_path = case_dir / "industry_analysis.json"
        tech = json.loads(tech_path.read_text(encoding="utf-8")) if tech_path.exists() else {}
        industry = json.loads(ind_path.read_text(encoding="utf-8")) if ind_path.exists() else {}
        tech_reasons = [str(value.get("rationale", "")) for value in (tech.get("technology_indicators") or {}).values() if value.get("rationale")]
        industry_reasons = [str(value.get("rationale", "")) for value in (industry.get("industry_indicators") or {}).values() if value.get("rationale")]
        evidence_ids = [str(item.get("evidence_id")) for item in (tech.get("evidence") or []) + (industry.get("evidence") or [])]
        rows.append({
            "company": metadata.get("enterprise_name", metadata["case_id"]),
            "system_status": metadata.get("assessment_status", metadata.get("status")),
            "technology_score": metadata.get("technology_score"),
            "industry_score": metadata.get("industry_score"),
            "overall_score": metadata.get("overall_score"),
            "technology_reasoning_summary": " | ".join(tech_reasons),
            "industry_reasoning_summary": " | ".join(industry_reasons),
            "evidence_ids": ",".join(dict.fromkeys(evidence_ids)),
            "technology_reasonableness": "",
            "industry_reasonableness": "",
            "evidence_support": "",
            "overclaiming": "",
            "trl_reasonableness": "",
            "notes": "",
        })
    with (output_root / "human_review_suggestions.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _manifest_cases() -> list[tuple[str, dict[str, Any], Path]]:
    result = []
    for case_dir in sorted((ROOT / "data" / "real_cases").iterdir()):
        if not case_dir.is_dir():
            continue
        manifest_path = case_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = case_dir / "sources" / "annual_report.pdf"
        documents = manifest.get("documents") or []
        downloaded = bool(documents) and all(item.get("status") == "downloaded" for item in documents)
        if downloaded and source.exists():
            result.append((case_dir.name, manifest, source))
    return result


async def run_case(
    case_id: str,
    manifest: dict[str, Any],
    source: Path,
    output_root: Path,
    endpoint: str,
    model: str,
    temperature: float,
) -> dict[str, Any]:
    case_out = output_root / case_id
    case_out.mkdir(parents=True, exist_ok=False)
    api_key = discover_api_key()
    transport = OpenAICompatibleHTTPTransport(
        api_key=api_key,
        timeout=float(os.getenv("KEHENG_LLM_TIMEOUT_SECONDS", "240")),
        temperature=temperature,
    )
    provider = OpenAICompatibleProvider(endpoint, model, transport=transport)
    request = TechnologyAnalysisInput(
        task_id=f"real-v09-{case_id}",
        enterprise_name=str(manifest.get("enterprise_name") or case_id),
        pdf_path=source,
        original_file_name=source.name,
    )
    metadata = {
        "schema_version": "1.1.0",
        "case_id": case_id,
        "enterprise_name": manifest.get("enterprise_name"),
        "provider": "Alibaba Cloud Bailian OpenAI Compatible",
        "endpoint": endpoint,
        "model": model,
        "temperature": temperature,
        "prompt_version": "v1.1-structured-json-technology",
        "agent_mode": "llm",
        "industry_agent_mode": "llm",
        "industry_prompt_version": "v1.1-structured-json-industry",
        "json_mode_enabled": bool(getattr(transport, "json_mode", True)),
        "repair_enabled": True,
        "source_manifest": str((ROOT / "data" / "real_cases" / case_id / "manifest.json").relative_to(ROOT)),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "api_key_configured": True,
        "provenance": build_run_provenance(
            Path(__file__), [SimpleNamespace(case_id=case_id, pdf_path=source)],
            [ROOT / "prompts" / "technology_agent_prompt.md", ROOT / "prompts" / "industry_agent_prompt.md"],
            {"endpoint": endpoint, "model": model, "temperature": temperature, "agent_mode": "llm", "industry_agent_mode": "llm", "json_mode": bool(getattr(transport, "json_mode", True)), "repair_enabled": True, "timeout_seconds_env": "KEHENG_LLM_TIMEOUT_SECONDS"},
            additional_data_paths=[ROOT / "data" / "real_cases" / case_id / "manifest.json"],
        ),
    }
    try:
        service = TechnologyAssessmentService(
            project_root=ROOT,
            runtime_root=case_out / "work",
            agent_mode="llm",
            industry_mode="llm",
            llm_provider=provider,
        )
        artifacts = await service.run_comprehensive_with_artifacts(request)
        _write(case_out / "technology_analysis.json", artifacts.technology_analysis.model_dump(mode="json"))
        _write(case_out / "industry_analysis.json", artifacts.industry_analysis.model_dump(mode="json"))
        _write(case_out / "technology_evaluation.json", artifacts.technology_evaluation.model_dump(mode="json"))
        _write(case_out / "industry_evaluation.json", artifacts.industry_evaluation)
        _write(case_out / "composite_evaluation.json", artifacts.comprehensive_evaluation)
        _write(case_out / "comprehensive_report.json", artifacts.report.model_dump(mode="json"))
        composite = artifacts.comprehensive_evaluation
        metadata.update({
            "status": "completed",
            "assessment_status": composite.get("assessment_status"),
            "technology_score": composite.get("technology_score"),
            "industry_score": composite.get("industry_score"),
            "overall_score": composite.get("overall_score"),
            "evidence_coverage": composite.get("evidence_coverage"),
            "provider_call_count": transport.call_count,
            "latency_seconds": transport.last_observation.get("latency_seconds"),
            "provider_observation": transport.last_observation,
            "observation_history": transport.observation_history,
        })
    except Exception as exc:  # noqa: BLE001 - preserve real-world failure
        metadata.update({
            "status": "pipeline_failed",
            "assessment_status": "pipeline_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "error_category": getattr(exc, "category", None) or transport.last_observation.get("error_category"),
            "provider_call_count": transport.call_count,
            "latency_seconds": transport.last_observation.get("latency_seconds"),
            "provider_observation": transport.last_observation,
            "observation_history": transport.observation_history,
        })
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write(case_out / "run_metadata.json", metadata)
    return metadata


async def main_async(args: argparse.Namespace) -> list[dict[str, Any]]:
    require_fresh_output_directory(args.output_root)
    results = []
    for case_id, manifest, source in _manifest_cases():
        if args.case_ids and case_id not in args.case_ids:
            continue
        print(f"[real-case] {case_id}: running llm mode", flush=True)
        result = await run_case(case_id, manifest, source, args.output_root, args.endpoint, args.model, args.temperature)
        print(f"[real-case] {case_id}: {result['status']}", flush=True)
        results.append(result)
    if args.case_ids:
        existing_path = args.output_root / "index.json"
        existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {"cases": []}
        by_id = {item.get("case_id"): item for item in existing.get("cases", [])}
        by_id.update({item.get("case_id"): item for item in results})
        results = [by_id[key] for key in sorted(by_id) if key]
    _write(args.output_root / "index.json", {"generated_at": datetime.now(timezone.utc).isoformat(), "cases": results})
    _write_human_review(args.output_root, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="必须是新目录；默认在 runtime/real_cases/run-* 下创建")
    parser.add_argument("--endpoint", default=os.getenv("KEHENG_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.getenv("KEHENG_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("KEHENG_LLM_TEMPERATURE", "0")))
    parser.add_argument("--case", dest="case_ids", action="append", default=[], help="仅运行指定 case_id，可重复传入")
    args = parser.parse_args()
    args.output_root = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "real_cases")
    results = asyncio.run(main_async(args))
    print(json.dumps({"completed": sum(item["status"] == "completed" for item in results), "failed": sum(item["status"] == "pipeline_failed" for item in results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
