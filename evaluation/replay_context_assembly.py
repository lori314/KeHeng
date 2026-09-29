"""Offline comparison of the legacy and balanced context assemblers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for item in (ROOT, BACKEND):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app.agents.technology_agent import TechnologyAgent, TechnologyAgentRequest
from app.rag.context_assembly import assemble_context
from app.rag.document_parser import DocumentSource, PdfDocumentParser
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.rag.knowledge_base import ChromaKnowledgeBase
from app.rag.bm25 import BM25KnowledgeBase
from app.rag.retrieval import KnowledgeBaseRetriever


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    # PDF extraction sometimes inserts a space after a Chinese semicolon;
    # removing only that boundary whitespace preserves all figures/units.
    return value.replace("; ", ";")


async def main() -> Path:
    out = ROOT / "runtime" / "review" / "context_assembly" / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    cases = json.loads((ROOT / "evaluation" / "retrieval_eval_cases_v2.json").read_text(encoding="utf-8"))["cases"]
    archived_path = ROOT / "runtime" / "review" / "paired_model" / "run-20260928T110017Z-2bc83078" / "paired_model_results.json"
    archived = json.loads(archived_path.read_text(encoding="utf-8")) if archived_path.is_file() else {"results": []}
    target_cases = [case for case in cases if case["case_id"] in {
        "nio_intellectual_property_en", "estun_intellectual_property_zh", "catl_technical_autonomy_zh"
    }]
    report_cases = []
    parser = PdfDocumentParser(chunk_size=520, chunk_overlap=80)
    for case in target_cases:
        company = case["company_id"]
        pdf = ROOT / "data" / "real_cases" / company / "sources" / "annual_report.pdf"
        if not pdf.is_file():
            report_cases.append({"case_id": case["case_id"], "status": "source_missing"})
            continue
        task_id = f"it05-{out.name}-{company}"
        source = DocumentSource(task_id=task_id, document_id=company, file_name=pdf.name, content_type="application/pdf", local_path=str(pdf))
        chunks = await parser.split(await parser.load(source))
        supports = case.get("candidate_supports", [])
        support_texts = [str(item["excerpt"]) for item in supports]
        prior = next((item for item in archived.get("results", []) if item.get("company_id") == company and item.get("retriever") == "hash"), None)
        prior_requests = (prior or {}).get("actual_model_requests", [])
        historical_tech_request = next((request for request in prior_requests if request.get("chunks") and request["chunks"][0].get("evidence_id") == "E1"), None)
        historical_context = [] if historical_tech_request is None else historical_tech_request.get("chunks", [])
        historical_text = "\n".join(str(item.get("excerpt", "")) for item in historical_context)
        def summarize(items):
            text = "\n".join(item.text for item in items)
            return {
                "selected_chunk_ids": [item.chunk_id for item in items],
                "selected_pages": [item.page_number for item in items],
                "characters": len(text),
                "support_text_complete": [_norm(quote) in _norm(text) for quote in support_texts],
                "support_texts": support_texts,
                "duplicate_text_share_estimate": _duplicate_share(items),
                "context": [{"chunk_id": item.chunk_id, "page_number": item.page_number, "text": item.text} for item in items],
            }
        for retrieval_mode in ("hash", "bm25"):
            if retrieval_mode == "hash":
                store = ChromaKnowledgeBase(out / "indexes" / company, LocalHashingEmbeddingProvider(1024), "keh_context_replay")
                await store.upsert(chunks)
                retriever = KnowledgeBaseRetriever(store)
            else:
                lexical = BM25KnowledgeBase()
                await lexical.upsert(chunks)
                retriever = KnowledgeBaseRetriever(lexical)
            agent = TechnologyAgent(retriever, query_mode="bilingual")
            candidates = await agent._retrieve(TechnologyAgentRequest(task_id=task_id, enterprise_name=case["enterprise_name"]))
            old_items, old_audit = assemble_context(candidates, strategy="legacy", max_chunks=8, max_characters=4160)
            new_items, new_audit = assemble_context(candidates, strategy="balanced_sentences_v1", max_chunks=8, max_characters=4160)
            report_cases.append({
            "case_id": case["case_id"], "company_id": company,
            "retriever": retrieval_mode,
            "label_status": case["label_status"], "candidate_labels_are_not_gold": True,
            "source_pdf_sha256": _sha(pdf), "task_id": task_id,
            "retrieval_config": {"method": "hash-1024 char-ngram", "query_mode": "generic bilingual", "queries": 4, "top_k_each": 3, "chunk_size": 520, "overlap": 80},
            "candidate_count": len(candidates),
            "candidate_diagnostic_only": [
                {"rank_by_score": index, "chunk_id": item.chunk_id, "page_number": item.page_number, "score": round(item.score, 6), "query_groups": item.metadata.get("retrieval_query_groups", ""), "candidate_label_quote_hit": any(_norm(quote) in _norm(item.text) for quote in support_texts)}
                for index, item in enumerate(sorted(candidates, key=lambda value: value.score, reverse=True), start=1)
            ],
            "iteration04_saved_hash_context": ({
                "available": historical_tech_request is not None,
                "chunk_count": len(historical_context),
                "characters": len(historical_text),
                "support_text_complete": [_norm(quote) in _norm(historical_text) for quote in support_texts],
                "chunks": [{"page_number": item.get("page_number"), "chunk_id": item.get("chunk_id"), "characters": len(item.get("excerpt", ""))} for item in historical_context],
                "evidence_source": "saved actual provider request context; no replay call",
            } if retrieval_mode == "hash" else {"available": False, "reason": "Iteration 04 paired model run contains only the hash context for this company"}),
            "legacy": {**summarize(old_items), "assembly": old_audit},
            "balanced": {**summarize(new_items), "assembly": new_audit},
            })
    result = {
        "schema_version": "1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "offline": True, "model_calls": 0,
        "budget": {"unit": "characters", "max_context_characters": 4160, "max_context_chunks": 8},
        "comparison": "same newly retrieved per-case candidate pool, parser, task-scoped hash retrieval and 4160-character budget; legacy global score order vs balanced query-group selection plus whole-sentence overlap dedup",
        "source_snapshot": {
            "retrieval_eval_cases_v2.json": _sha(ROOT / "evaluation" / "retrieval_eval_cases_v2.json"),
            "query_definitions": _sha(ROOT / "evaluation" / "retrieval_indicator_definitions.json"),
            "replay_context_assembly.py": _sha(Path(__file__)),
            "context_assembly.py": _sha(BACKEND / "app" / "rag" / "context_assembly.py"),
            "technology_agent.py": _sha(BACKEND / "app" / "agents" / "technology_agent.py"),
            "document_parser.py": _sha(BACKEND / "app" / "rag" / "document_parser.py"),
            "knowledge_base.py": _sha(BACKEND / "app" / "rag" / "knowledge_base.py"),
            "embedding.py": _sha(BACKEND / "app" / "rag" / "embedding.py"),
        },
        "historical_comparison_source": {"path": str(archived_path.relative_to(ROOT)), "sha256": _sha(archived_path) if archived_path.is_file() else None},
        "cases": report_cases,
    }
    (out / "context_assembly_comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _duplicate_share(items) -> float:
    sentences = []
    import re
    for item in items:
        sentences.extend(re.findall(r"[^。！？!?\n]+[。！？!?]?", item.text))
    normalized = [" ".join(value.split()) for value in sentences if value.strip()]
    if not normalized:
        return 0.0
    return round(1 - len(set(normalized)) / len(normalized), 4)


if __name__ == "__main__":
    print(asyncio.run(main()))
