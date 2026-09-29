"""Offline A/B/C/D retrieval comparison on saved company PDFs."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.rag.document_parser import DocumentSource, PdfDocumentParser  # noqa: E402
from app.rag.bm25 import BM25KnowledgeBase, rank_texts  # noqa: E402
from app.rag.query_definitions import indicator_queries  # noqa: E402
from app.rag.embedding import LocalHashingEmbeddingProvider  # noqa: E402
from app.rag.knowledge_base import ChromaKnowledgeBase  # noqa: E402
from app.rag.retrieval import RetrievalQuery  # noqa: E402
from app.agents.technology_agent import TechnologyAgent  # noqa: E402
from app.agents.industry_agent import IndustryAgent  # noqa: E402
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402

CASES_PATH = ROOT / "evaluation" / "retrieval_eval_cases.json"
CASES_V2_PATH = ROOT / "evaluation" / "retrieval_eval_cases_v2.json"
DEFINITIONS_PATH = ROOT / "evaluation" / "retrieval_indicator_definitions.json"
MAX_PER_QUERY = 10
MAX_QUERIES = 4
CONTEXT_CHUNKS = 8
CONTEXT_CHAR_BUDGET = 520 * CONTEXT_CHUNKS
RRF_K = 60


class BM25:
    """Compatibility helper for older local callers; runner uses BM25KnowledgeBase."""
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.texts, self.k1, self.b = texts, k1, b

    def rank(self, query: str, limit: int) -> list[tuple[int, float]]:
        return rank_texts(query, self.texts, limit, k1=self.k1, b=self.b)


def normalize(text: str) -> str:
    # Strict exact-span normalization only: Unicode compatibility forms, case,
    # and whitespace may vary. Punctuation, numerals, signs, units, and negation
    # remain part of the comparison.
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _normalized_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return normalized characters with conservative source character spans."""
    chars: list[str] = []
    offsets: list[tuple[int, int]] = []
    for source_index, source_char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", source_char).casefold()
        for char in normalized:
            if char.isspace():
                if chars and chars[-1] != " ":
                    chars.append(" ")
                    offsets.append((source_index, source_index + 1))
                elif chars and chars[-1] == " ":
                    offsets[-1] = (offsets[-1][0], source_index + 1)
            else:
                chars.append(char)
                offsets.append((source_index, source_index + 1))
    while chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


def _locate_anchor(page_text: str, excerpt: str) -> dict[str, Any] | None:
    normalized_page, offsets = _normalized_with_offsets(page_text)
    normalized_excerpt = normalize(excerpt)
    if not normalized_excerpt:
        return None
    at = normalized_page.find(normalized_excerpt)
    if at < 0:
        return None
    return {
        "normalized_start": at,
        "normalized_end": at + len(normalized_excerpt),
        "source_char_start": offsets[at][0],
        "source_char_end": offsets[at + len(normalized_excerpt) - 1][1],
        "matched_text": page_text[offsets[at][0]:offsets[at + len(normalized_excerpt) - 1][1]],
        "normalization": "NFKC + casefold + whitespace collapse; punctuation/numbers/units/negation preserved",
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data_path = CASES_V2_PATH if CASES_V2_PATH.is_file() else CASES_PATH
    data = json.loads(data_path.read_text(encoding="utf-8"))
    definitions = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    return data["cases"], definitions["definitions"]


def _queries(domain: str, indicator: str, definitions: dict[str, list[str]]) -> list[str]:
    # The original method reproduces the exact four search strings used by the Agent.
    if domain == "technology":
        return list(TechnologyAgent._queries)
    return list(IndustryAgent._queries)


def _indicator_queries(indicator: str, definitions: dict[str, list[str]]) -> list[str]:
    queries = indicator_queries(indicator)
    if len(queries) != MAX_QUERIES:
        raise ValueError(f"expected {MAX_QUERIES} generic queries for {indicator}")
    return queries


def _rrf(vector_lists: list[list[tuple[int, float]]], bm25_lists: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
    scores: defaultdict[int, float] = defaultdict(float)
    for vector, lexical in zip(vector_lists, bm25_lists, strict=True):
        for ranked in (vector, lexical):
            for rank, (index, _) in enumerate(ranked, start=1):
                scores[index] += 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


async def _compare(output_root: Path) -> dict[str, Any]:
    require_fresh_output_directory(output_root)
    cases, definitions = _load()
    companies = sorted({item["company_id"] for item in cases})
    chunks_by_company: dict[str, list[Any]] = {}
    pages_by_company: dict[str, dict[int, str]] = {}
    bm25_by_company: dict[str, BM25KnowledgeBase] = {}
    store_by_company: dict[str, ChromaKnowledgeBase] = {}
    task_by_company: dict[str, str] = {}
    pdf_hashes: dict[str, str] = {}
    parser_config = {"parser": "pymupdf-v1", "chunk_size": 520, "chunk_overlap": 80}
    parser = PdfDocumentParser(chunk_size=520, chunk_overlap=80)

    for company_id in companies:
        company_case = next(item for item in cases if item["company_id"] == company_id)
        pdf_path = ROOT / "data" / "real_cases" / company_id / "sources" / "annual_report.pdf"
        if not pdf_path.is_file():
            raise FileNotFoundError(f"missing existing source PDF: {pdf_path}")
        source_manifest_path = ROOT / "data" / "real_cases" / company_id / "manifest.json"
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        source_entry = next((entry for entry in source_manifest.get("documents", []) if entry.get("file_name") == "sources/annual_report.pdf"), None)
        actual_pdf_hash = _sha(pdf_path)
        if source_entry is None or source_entry.get("sha256") != actual_pdf_hash:
            raise ValueError(f"source PDF does not match manifest SHA-256 for {company_id}")
        task_id = f"iteration02-{output_root.name}-{company_id}"
        task_by_company[company_id] = task_id
        pdf_hashes[company_id] = actual_pdf_hash
        source = DocumentSource(task_id=task_id, document_id=company_id, file_name="annual_report.pdf", content_type="application/pdf", local_path=str(pdf_path))
        parsed = await parser.load(source)
        chunks = await parser.split(parsed)
        chunks_by_company[company_id] = chunks
        pages_by_company[company_id] = {page.page_number: page.text for page in parsed.pages}
        lexical = BM25KnowledgeBase()
        await lexical.upsert(chunks)
        bm25_by_company[company_id] = lexical
        store = ChromaKnowledgeBase(output_root / "indexes" / company_id, LocalHashingEmbeddingProvider(dimensions=1024), collection_name="keheng_task_evidence")
        await store.upsert(chunks)
        store_by_company[company_id] = store

    case_records: list[dict[str, Any]] = []
    for item in cases:
        company = item["company_id"]
        chunks = chunks_by_company[company]
        chunk_index = {chunk.chunk_id: i for i, chunk in enumerate(chunks)}
        bm25 = bm25_by_company[company]
        candidate_supports = item["candidate_supports"]
        anchors: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for anchor_index, support in enumerate(candidate_supports, start=1):
            page_number = int(support["page_number"])
            page_text = pages_by_company[company].get(page_number, "")
            location = _locate_anchor(page_text, support["excerpt"])
            if location is None:
                unresolved.append(f"page {page_number}: exact normalized support span not found in extracted source text")
                continue
            normalized_quote = normalize(support["excerpt"])
            matching_chunks = [
                chunk for chunk in chunks
                if chunk.page_number == page_number and normalized_quote in normalize(chunk.text)
            ]
            anchor_id = hashlib.sha256(
                f"{pdf_hashes[company]}|{page_number}|{location['source_char_start']}|{location['source_char_end']}|{normalized_quote}".encode("utf-8")
            ).hexdigest()[:24]
            anchors.append({
                "anchor_id": anchor_id,
                "source_sha256": pdf_hashes[company],
                "page_number": page_number,
                "excerpt": support["excerpt"],
                "group": str(support.get("group", "default")),
                "location": location,
                "all_containing_chunk_ids": [chunk.chunk_id for chunk in matching_chunks],
                "old_evaluator_first_matching_chunk_id": matching_chunks[0].chunk_id if matching_chunks else None,
                "legacy_referenced_chunk_id": support.get("legacy_chunk_id"),
                "source_anchor_resolved": True,
                "parser_chunk_resolved": bool(matching_chunks),
            })
            if not matching_chunks:
                unresolved.append(f"page {page_number}: source anchor exists but no parser chunk contains the complete span")
        original_queries = _queries(item["domain"], item["indicator"], definitions)
        generic_queries = _indicator_queries(item["indicator"], definitions)
        if len(original_queries) != MAX_QUERIES or len(generic_queries) != MAX_QUERIES:
            raise ValueError("the four retrieval methods must use the same query count")
        methods: dict[str, list[tuple[int, float]]] = {}
        latency: dict[str, float] = {}

        async def vector_rank(query_list: list[str]) -> tuple[list[list[tuple[int, float]]], float]:
            started = time.perf_counter()
            results: list[list[tuple[int, float]]] = []
            for query in query_list:
                found = await store_by_company[company].query(RetrievalQuery(task_id=task_by_company[company], query=query, top_k=MAX_PER_QUERY, document_ids=[company]))
                results.append([(chunk_index[evidence.chunk_id], float(evidence.score)) for evidence in found if evidence.chunk_id in chunk_index])
            return results, (time.perf_counter() - started) * 1000

        async def bm25_rank(query_list: list[str]) -> tuple[list[list[tuple[int, float]]], float]:
            started = time.perf_counter()
            results = []
            for query in query_list:
                found = await bm25.query(RetrievalQuery(task_id=task_by_company[company], query=query, top_k=MAX_PER_QUERY, document_ids=[company]))
                results.append([(chunk_index[item.chunk_id], float(item.score)) for item in found if item.chunk_id in chunk_index])
            return results, (time.perf_counter() - started) * 1000

        orig_vectors, latency["A"] = await vector_rank(original_queries)
        vector_lists, latency["B"] = await vector_rank(generic_queries)
        lexical_lists, latency["C"] = await bm25_rank(generic_queries)
        methods["A"] = _merge_max(orig_vectors)
        methods["B"] = _merge_max(vector_lists)
        methods["C"] = _merge_max(lexical_lists)
        started = time.perf_counter()
        methods["D"] = _rrf(vector_lists, lexical_lists)
        latency["D"] = latency["B"] + latency["C"] + (time.perf_counter() - started) * 1000

        labels_resolved = bool(anchors) and not unresolved
        method_records = {}
        for method, ranked in methods.items():
            method_records[method] = _measure(ranked, chunks, anchors, labels_resolved, latency[method])
        case_records.append({
            "case_id": item["case_id"], "company_id": company, "indicator": item["indicator"], "domain": item["domain"],
            "language": item["language"], "split": item["split"], "label_status": item["label_status"],
            "task_id": task_by_company[company], "query_source": {"A": "current Agent domain query set", "B-D": "generic indicator definition bilingual query set"},
            "query_count": MAX_QUERIES, "per_query_top_n": MAX_PER_QUERY, "candidate_pool_budget": MAX_QUERIES * MAX_PER_QUERY,
            "needs_multiple_chunks": item["needs_multiple_chunks"], "candidate_anchor_count": len(anchors),
            "evidence_anchors": anchors, "candidate_anchor_resolution_errors": unresolved, "metrics": method_records,
            "ranked_top_10": {method: [_chunk_record(chunks[index], score) for index, score in ranked[:10]] for method, ranked in methods.items()},
            "generic_query_sha256": hashlib.sha256(json.dumps(generic_queries, ensure_ascii=False).encode()).hexdigest(),
            "original_query_sha256": hashlib.sha256(json.dumps(original_queries, ensure_ascii=False).encode()).hexdigest(),
        })

    summaries = _summaries(case_records)
    manifest = {
        "schema_version": "2.0.0", "created_at": datetime.now(timezone.utc).isoformat(), "offline": True,
        "network_or_model_calls": 0, "input_files": {str(CASES_PATH.relative_to(ROOT)): _sha(CASES_PATH), str(CASES_V2_PATH.relative_to(ROOT)): _sha(CASES_V2_PATH), str(DEFINITIONS_PATH.relative_to(ROOT)): _sha(DEFINITIONS_PATH)},
        "company_source_pdf_sha256": pdf_hashes, "company_source_sha_matches_manifest": True, "parser_config": parser_config,
        "embedding": {"implementation": "LocalHashingEmbeddingProvider", "analyzer": "char", "ngram_range": [2, 4], "dimensions": 1024, "collection_name": "keheng_task_evidence"},
        "bm25": {"implementation": "backend/app/rag/bm25.py shared BM25KnowledgeBase", "tokenizer": "NFKC + casefold Latin tokens + overlapping CJK bigrams", "k1": 1.5, "b": 0.75},
        "query_count": MAX_QUERIES, "per_query_candidate_limit": MAX_PER_QUERY, "dedup": "chunk_id; A/B/C keep highest score across queries", "RRF": {"k": RRF_K, "combine": "sum reciprocal ranks for each of four same query slots and both retrievers"},
        "context_budget": {"maximum_chunks": CONTEXT_CHUNKS, "maximum_characters": CONTEXT_CHAR_BUDGET, "selection": "top 8 aggregate candidates, truncate last chunk at remaining char budget"},
        "task_id_isolation": "one run-specific task_id per company; every Chroma query filters exact task_id and document_id",
        "heldout_policy": "heldout company queries were defined before this run and were not used to adjust query definitions or RRF parameters",
        "label_warning": "All eight evidence labels remain candidate_unconfirmed. Candidate metrics are provisional; confirmed-label recall is not estimable. AI text review is not human confirmation.",
        "case_file_version": "v2 anchor-based overlay; v1 source preserved",
        "runtime_source_fingerprint": _source_fingerprint([Path(__file__), ROOT / "evaluation" / "prepare_iteration04_case_review.py", ROOT / "backend" / "app" / "rag" / "bm25.py", ROOT / "backend" / "app" / "rag" / "query_definitions.py", ROOT / "backend" / "app" / "rag" / "document_parser.py", ROOT / "backend" / "app" / "rag" / "embedding.py", ROOT / "backend" / "app" / "rag" / "knowledge_base.py", ROOT / "backend" / "app" / "agents" / "technology_agent.py", ROOT / "backend" / "app" / "agents" / "industry_agent.py", CASES_PATH, CASES_V2_PATH, DEFINITIONS_PATH]),
    }
    result = {"schema_version": "2.0.0", "methods": {"A": "original hash retrieval + current original Agent queries", "B": "original hash retrieval + generic bilingual indicator queries", "C": "BM25 + generic bilingual indicator queries", "D": "RRF(hash,BM25) + generic bilingual indicator queries"}, "summary": summaries, "cases": case_records, "manifest": manifest}
    _write_json(output_root / "retrieval_comparison.json", result)
    _write_json(output_root / "retrieval_summary.json", summaries)
    _write_csv(output_root / "retrieval_per_case.csv", case_records)
    _write_pending_review(output_root / "pending_evidence_review.csv", cases)
    return {"output_root": str(output_root), "summary": summaries, "case_count": len(case_records), "manifest": manifest}


def _merge_max(lists: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
    best: dict[int, float] = {}
    for ranking in lists:
        for index, score in ranking:
            best[index] = max(best.get(index, float("-inf")), score)
    return sorted(best.items(), key=lambda item: (-item[1], item[0]))


def _measure(ranked: list[tuple[int, float]], chunks: list[Any], anchors: list[dict[str, Any]], labels_resolved: bool, elapsed_ms: float) -> dict[str, Any]:
    indices = [index for index, _ in ranked]
    unique_anchors = {anchor["anchor_id"]: anchor for anchor in anchors}
    anchors = list(unique_anchors.values())
    metrics: dict[str, Any] = {
        "candidate_label_status": "provisional_unconfirmed",
        "candidate_support_resolved": labels_resolved,
        "unanswerable_items_confirmed": 0,
        "primary_hit_definition": "full normalized support excerpt contained in a returned chunk; exact quote only, no fuzzy/equivalence inference",
        "support_hit_normalization": "NFKC + casefold + whitespace collapse; preserves punctuation, numbers, signs, units and negation",
    }
    anchor_quote = {anchor["anchor_id"]: normalize(anchor["excerpt"]) for anchor in anchors}
    old_evaluator_ids = {
        anchor["anchor_id"]: ({str(anchor["old_evaluator_first_matching_chunk_id"])} if anchor.get("old_evaluator_first_matching_chunk_id") else set())
        for anchor in anchors
    }
    legacy_ids = {anchor["anchor_id"]: anchor.get("legacy_referenced_chunk_id") for anchor in anchors}
    all_containing_ids = {
        anchor["anchor_id"]: set(anchor["all_containing_chunk_ids"])
        for anchor in anchors
    }
    returned_ids = {chunks[index].chunk_id for index in indices}
    support_matches: dict[str, list[str]] = {}
    for anchor in anchors:
        quote = anchor_quote[anchor["anchor_id"]]
        support_matches[anchor["anchor_id"]] = [
            chunks[index].chunk_id for index in indices if quote in normalize(chunks[index].text)
        ]
    anchor_hit_ranks = {
        anchor["anchor_id"]: min(
            (rank for rank, index in enumerate(indices, start=1)
             if anchor_quote[anchor["anchor_id"]] in normalize(chunks[index].text)),
            default=None,
        )
        for anchor in anchors
    }
    anchor_id_hits = {
        anchor["anchor_id"]: (bool(old_evaluator_ids[anchor["anchor_id"]] & returned_ids) if old_evaluator_ids[anchor["anchor_id"]] else None)
        for anchor in anchors
    }
    legacy_id_comparable = {
        anchor["anchor_id"]: bool(legacy_ids[anchor["anchor_id"]] and legacy_ids[anchor["anchor_id"]] in {chunk.chunk_id for chunk in chunks})
        for anchor in anchors
    }
    top10_pages = {chunks[index].page_number for index in indices[:10]}
    context_indices: list[int] = []
    context_chunks: list[dict[str, Any]] = []
    context_chars = 0
    for index in indices:
        chunk = chunks[index]
        remaining = CONTEXT_CHAR_BUDGET - context_chars
        if remaining <= 0:
            break
        sent_text = chunk.text[:remaining]
        context_indices.append(index)
        context_chunks.append({"chunk_id": chunk.chunk_id, "page_number": chunk.page_number, "text": sent_text})
        context_chars += len(sent_text)
        if len(chunk.text) > remaining:
            break
        if len(context_indices) >= CONTEXT_CHUNKS:
            break
    context_set = set(context_indices)
    context_ids = {chunks[index].chunk_id for index in context_indices}
    context_anchor_hits = {
        anchor["anchor_id"]: any(
            anchor_quote[anchor["anchor_id"]] in normalize(context_chunk["text"])
            for context_chunk in context_chunks
        )
        for anchor in anchors
    }
    ranked_anchor_hits = [anchor_hit_ranks[a["anchor_id"]] for a in anchors if anchor_hit_ranks[a["anchor_id"]] is not None]
    anchor_count = len(anchors)
    for k in (3, 5, 10):
        selected_ids = {chunks[index].chunk_id for index in indices[:k]}
        selected_indices = set(indices[:k])
        text_hit_n = sum(
            any(anchor_quote[anchor["anchor_id"]] in normalize(chunks[index].text) for index in selected_indices)
            for anchor in anchors
        )
        id_hit_rows = [a for a in anchors if old_evaluator_ids[a["anchor_id"]]]
        id_hit_n = sum(bool(old_evaluator_ids[a["anchor_id"]] & selected_ids) for a in id_hit_rows)
        target_pages = {int(a["page_number"]) for a in anchors}
        selected_pages = {chunks[index].page_number for index in indices[:k]}
        page_hit_n = len(target_pages & selected_pages)
        metrics[f"support_text_recall_at_{k}"] = text_hit_n / anchor_count if labels_resolved and anchor_count else None
        metrics[f"old_evaluator_first_chunk_id_recall_at_{k}"] = id_hit_n / len(id_hit_rows) if labels_resolved and id_hit_rows else None
        metrics[f"legacy_annotation_chunk_id_comparable_n_at_{k}"] = sum(legacy_id_comparable.values())
        metrics[f"source_page_recall_at_{k}"] = page_hit_n / len(target_pages) if labels_resolved and target_pages else None
        metrics[f"support_text_hit_count_at_{k}"] = text_hit_n
        metrics[f"old_evaluator_first_chunk_id_hit_count_at_{k}"] = id_hit_n
        metrics[f"candidate_at_least_one_anchor_hit_at_{k}"] = text_hit_n > 0
    hit_ranks = [rank for rank in ranked_anchor_hits]
    metrics["support_text_mrr"] = (1 / min(hit_ranks)) if hit_ranks and labels_resolved else 0.0 if labels_resolved else None
    metrics["old_evaluator_first_chunk_id_hits_top_10"] = sum(value is True for value in anchor_id_hits.values())
    metrics["support_text_hits_top_10"] = sum(rank is not None and rank <= 10 for rank in anchor_hit_ranks.values())
    metrics["support_text_hits_final_context"] = sum(context_anchor_hits.values())
    metrics["support_text_recall_final_context"] = sum(context_anchor_hits.values()) / anchor_count if labels_resolved and anchor_count else None
    metrics["candidate_at_least_one_anchor_in_final_context"] = int(any(context_anchor_hits.values()))
    metrics["support_hit_details"] = [
        {
            "anchor_id": anchor["anchor_id"],
            "exact_support_text_hit_ranks": [i + 1 for i, index in enumerate(indices) if anchor_quote[anchor["anchor_id"]] in normalize(chunks[index].text)],
            "returned_chunks_containing_full_support_text": support_matches[anchor["anchor_id"]],
            "all_current_chunks_containing_full_support_text": sorted(all_containing_ids[anchor["anchor_id"]]),
            "legacy_annotation_chunk_id": legacy_ids[anchor["anchor_id"]],
            "legacy_annotation_chunk_id_present_in_current_run_index": legacy_id_comparable[anchor["anchor_id"]],
            "legacy_annotation_chunk_id_cross_task_comparison": "not comparable: chunk ID is task-scoped",
            "old_evaluator_first_matching_chunk_id_this_run": anchor.get("old_evaluator_first_matching_chunk_id"),
            "old_evaluator_first_chunk_id_hit": anchor_id_hits[anchor["anchor_id"]],
            "source_page_hit_top_10": anchor["page_number"] in top10_pages,
            "full_support_text_in_final_context": context_anchor_hits[anchor["anchor_id"]],
            "same_page_without_exact_quote_candidates": [chunks[index].chunk_id for index in indices[:10] if chunks[index].page_number == anchor["page_number"] and anchor_quote[anchor["anchor_id"]] not in normalize(chunks[index].text)],
            "equivalent_or_rephrased_support": "not auto-judged; candidates require human review",
        }
        for anchor in anchors
    ]
    metrics["returned_chunks"] = len(indices)
    metrics["returned_text_characters_top_10"] = sum(len(chunks[index].text) for index in indices[:10])
    metrics["context_budget"] = {"chunks": len(context_indices), "characters": context_chars, "max_chunks": CONTEXT_CHUNKS, "max_characters": CONTEXT_CHAR_BUDGET}
    metrics["actual_context_chunks"] = context_chunks
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for anchor in anchors:
        groups[str(anchor.get("group", "default"))].append(anchor)
    metrics["multi_passage_coverage"] = {group: {"required_support_anchors": len(values), "covered_support_anchors": sum(context_anchor_hits[value["anchor_id"]] for value in values), "complete": all(context_anchor_hits[value["anchor_id"]] for value in values)} for group, values in groups.items()}
    metrics["retrieval_latency_ms"] = round(elapsed_ms, 3)
    return metrics


def _chunk_record(chunk: Any, score: float) -> dict[str, Any]:
    return {"task_id": chunk.task_id, "document_id": chunk.document_id, "document_name": chunk.document_name, "page_number": chunk.page_number, "chunk_id": chunk.chunk_id, "locator": chunk.locator, "score": round(score, 8), "text": chunk.text}


def _summaries(records: list[dict[str, Any]]) -> dict[str, Any]:
    methods = ("A", "B", "C", "D")
    summaries: dict[str, Any] = {"confirmed_label_n": 0, "candidate_label_n": len(records), "no_confirmed_unanswerable_n": 0, "label_warning": "全部为待确认候选标签；以下数值是暂定候选召回率，不是已验证提升。", "development": {}, "heldout": {}, "by_language": {}, "by_indicator": {}, "by_company": {}}
    groups: dict[str, list[dict[str, Any]]] = {}
    for split in ("development", "heldout"):
        groups[split] = [record for record in records if record["split"] == split]
    for field in ("language", "indicator", "company_id"):
        for value in sorted({record[field] for record in records}):
            groups[f"{field}:{value}"] = [record for record in records if record[field] == value]
    for key, subset in groups.items():
        output = {}
        for method in methods:
            method_rows = [row["metrics"][method] for row in subset]
            output[method] = {metric: _mean_non_null([row[metric] for row in method_rows if metric in row]) for metric in (
                "support_text_recall_at_3", "support_text_recall_at_5", "support_text_recall_at_10",
                "old_evaluator_first_chunk_id_recall_at_3", "old_evaluator_first_chunk_id_recall_at_5", "old_evaluator_first_chunk_id_recall_at_10",
                "source_page_recall_at_3", "source_page_recall_at_5", "source_page_recall_at_10",
                "support_text_mrr", "support_text_recall_final_context", "candidate_at_least_one_anchor_in_final_context", "retrieval_latency_ms", "returned_text_characters_top_10",
            )}
        if key == "development": summaries["development"] = output
        elif key == "heldout": summaries["heldout"] = output
        elif key.startswith("language:"): summaries["by_language"][key.split(":",1)[1]] = output
        elif key.startswith("indicator:"): summaries["by_indicator"][key.split(":",1)[1]] = output
        elif key.startswith("company_id:"): summaries["by_company"][key.split(":",1)[1]] = output
    return summaries


def _mean_non_null(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return round(sum(numeric) / len(numeric), 4) if numeric else None


def _source_fingerprint(paths: list[Path]) -> dict[str, Any]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": _sha(path)} for path in sorted(set(paths))]
    return {"algorithm": "sha256", "files": files, "digest": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), "note": "explicit current source set; no Git commit exists"}


def _write_pending_review(path: Path, cases: list[dict[str, Any]]) -> None:
    import csv
    with path.open("x", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "company_id", "split", "indicator", "language", "question", "candidate_excerpt", "page_number", "proposed_label", "needs_multiple_chunks", "pending_reason", "label_status"])
        writer.writeheader()
        for case in cases:
            for support in case["candidate_supports"]:
                writer.writerow({"case_id": case["case_id"], "company_id": case["company_id"], "split": case["split"], "indicator": case["indicator"], "language": case["language"], "question": case["question"], "candidate_excerpt": support["excerpt"], "page_number": support["page_number"], "proposed_label": "support_candidate", "needs_multiple_chunks": case["needs_multiple_chunks"], "pending_reason": case["pending_reason"], "label_status": case["label_status"]})


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    import csv
    fields = ["case_id", "company_id", "indicator", "domain", "language", "split", "label_status", "method", "support_text_recall_at_3", "support_text_recall_at_5", "support_text_recall_at_10", "old_evaluator_first_chunk_id_recall_at_3", "old_evaluator_first_chunk_id_recall_at_5", "old_evaluator_first_chunk_id_recall_at_10", "source_page_recall_at_10", "support_text_mrr", "support_text_recall_final_context", "latency_ms", "top10_text_characters"]
    with path.open("x", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            for method, metrics in record["metrics"].items():
                writer.writerow({"case_id": record["case_id"], "company_id": record["company_id"], "indicator": record["indicator"], "domain": record["domain"], "language": record["language"], "split": record["split"], "label_status": record["label_status"], "method": method, **{name: metrics.get(name) for name in fields if name in metrics}, "latency_ms": metrics["retrieval_latency_ms"], "top10_text_characters": metrics["returned_text_characters_top_10"]})


async def _main_async(output_root: Path) -> dict[str, Any]:
    return await _compare(output_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="必须是新目录；默认创建 runtime/review/retrieval/run-*")
    args = parser.parse_args()
    output = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "review" / "retrieval")
    print(json.dumps(asyncio.run(_main_async(output)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
