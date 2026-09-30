"""Extract one-source atomic technology facts from raw GENERAL chunks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.llm import StructuredJSONModel, StructuredModelError
from app.knowledge.contracts import KnowledgeChunk, Source, SourceVersion
from app.knowledge.semantic.contracts import (
    FactExtractionBatchOutput,
    FactExtractionReport,
    SourceQuality,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.evidence_selector import semantic_citation_context
from app.knowledge.semantic.structured_call import complete_contract

FACT_EXTRACTION_BATCH_SIZE = 3
FACT_EXTRACTION_MAX_CONCURRENCY = 2


@dataclass
class _BatchResult:
    index: int
    chunks: list[KnowledgeChunk]
    output: FactExtractionBatchOutput | None = None
    category: str | None = None


class TechnologyFactExtractor:
    def __init__(
        self,
        model: StructuredJSONModel,
        registry: KnowledgeSemanticRegistry,
        *,
        processor_version: str,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        self.processor_version = processor_version
        path = prompt_path or _prompt_path("technology_fact_extractor_prompt.md")
        self.prompt = Path(path).read_text(encoding="utf-8")
        self.last_report = FactExtractionReport(
            batch_size=FACT_EXTRACTION_BATCH_SIZE,
            max_concurrency=FACT_EXTRACTION_MAX_CONCURRENCY,
        )
        self.last_execution_trace: dict[str, object] = {}

    async def extract(
        self,
        *,
        company_id: str,
        domain_profile: TechnologyDomainProfile,
        selection: TechnologyTemplateSelection,
        chunks: list[KnowledgeChunk],
        sources: dict[str, Source],
        versions: dict[str, SourceVersion],
    ) -> tuple[list[TechnologyFact], list[str], list[str], list[str], FactExtractionReport]:
        if not selection.selected_template_ids or not chunks:
            report = FactExtractionReport(
                input_chunk_count=len(chunks),
                batch_size=FACT_EXTRACTION_BATCH_SIZE,
                max_concurrency=FACT_EXTRACTION_MAX_CONCURRENCY,
            )
            self._save_trace(report)
            return [], [], [], [], report

        template_data = [
            self.registry.template_by_id[item].model_dump(mode="json")
            for item in selection.selected_template_ids
        ]
        allowed_fact_types = [
            {
                "id": item.id,
                "description": item.description,
                "category": item.category,
            }
            for item in self.registry.fact_types.fact_types
        ]
        contexts_by_id: dict[str, dict] = {}
        quality_by_id: dict[str, SourceQuality] = {}
        for chunk in chunks:
            source = sources[chunk.source_id]
            version = versions[chunk.source_version_id or ""]
            quality = source_quality(source, chunk, version)
            quality_by_id[chunk.chunk_id] = quality
            contexts_by_id[chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "source_quality": quality.model_dump(mode="json"),
                "citation": semantic_citation_context(chunk),
            }

        batches = [
            chunks[start : start + FACT_EXTRACTION_BATCH_SIZE]
            for start in range(0, len(chunks), FACT_EXTRACTION_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(FACT_EXTRACTION_MAX_CONCURRENCY)

        async def run_batch(index: int, batch: list[KnowledgeChunk]) -> _BatchResult:
            result = _BatchResult(index=index, chunks=batch)
            async with semaphore:
                try:
                    result.output = await complete_contract(
                        self.model,
                        self.prompt,
                        {
                            "company_id": company_id,
                            "domain_profile": domain_profile.model_dump(mode="json"),
                            "selected_templates": template_data,
                            "allowed_fact_types": allowed_fact_types,
                            "general_chunks": [contexts_by_id[item.chunk_id] for item in batch],
                        },
                        FactExtractionBatchOutput,
                        stage=f"technology fact extractor batch {index + 1}",
                    )
                except Exception as exc:
                    result.category = (
                        exc.category if isinstance(exc, StructuredModelError)
                        else "timeout" if isinstance(exc, TimeoutError)
                        else "network" if isinstance(exc, ConnectionError)
                        else "unexpected_error"
                    )
            return result

        # gather preserves input order, and results are sorted again below as an explicit invariant.
        results = await asyncio.gather(*(run_batch(i, batch) for i, batch in enumerate(batches)))
        results.sort(key=lambda item: item.index)
        failed = [item for item in results if item.output is None]
        error_categories: dict[str, int] = {}
        for item in failed:
            category = item.category or "unexpected_error"
            error_categories[category] = error_categories.get(category, 0) + 1

        valid_facts: list[TechnologyFact] = []
        rejected_evidence: list[str] = []
        rejected_tags: list[str] = []
        rejected_fact_types: Counter[str] = Counter()
        gaps: list[str] = []
        selected_chunks_by_id = {item.chunk_id: item for item in chunks}
        domain_ids = set(domain_profile.primary_domains + domain_profile.secondary_domains)
        selected_ids = set(selection.selected_template_ids)
        seen_fact_ids: set[str] = set()
        for result in results:
            if result.output is None:
                continue
            batch_chunk_ids = {item.chunk_id for item in result.chunks}
            chunks_by_id = {item.chunk_id: item for item in result.chunks}
            gaps.extend(result.output.information_gaps)
            for draft in result.output.facts:
                canonical_fact_type = draft.fact_type.strip().casefold()
                if canonical_fact_type not in self.registry.allowed_technology_fact_types:
                    rejected_fact_types[canonical_fact_type or "<empty>"] += 1
                    continue
                if draft.source_chunk_id not in batch_chunk_ids:
                    rejected_evidence.append(draft.source_chunk_id)
                    continue
                chunk = chunks_by_id.get(draft.source_chunk_id)
                if chunk is None or draft.source_chunk_id not in selected_chunks_by_id:
                    rejected_evidence.append(draft.source_chunk_id)
                    continue
                if not set(draft.domain_tags).issubset(domain_ids) or not set(draft.template_tags).issubset(selected_ids):
                    rejected_tags.append(draft.source_chunk_id)
                    continue
                fact_id = stable_fact_id(
                    company_id,
                    chunk.chunk_id,
                    {
                        "subject": _normalized(draft.subject),
                        "predicate": _normalized(draft.predicate),
                        "object_value": _normalized(draft.object_value),
                        "fact_type": canonical_fact_type,
                        "event_time": draft.event_time.isoformat() if draft.event_time else None,
                        "quantitative_value": draft.quantitative_value,
                        "quantitative_unit": draft.quantitative_unit,
                        "domain_tags": sorted(set(draft.domain_tags)),
                        "template_tags": sorted(set(draft.template_tags)),
                    },
                    self.processor_version,
                    self.registry.templates.registry_version,
                )
                if fact_id in seen_fact_ids:
                    continue
                seen_fact_ids.add(fact_id)
                valid_facts.append(
                    TechnologyFact(
                        fact_id=fact_id,
                        company_id=company_id,
                        subject=draft.subject.strip(),
                        predicate=draft.predicate.strip(),
                        object_value=draft.object_value.strip(),
                        fact_type=canonical_fact_type,
                        event_time=draft.event_time,
                        quantitative_value=draft.quantitative_value,
                        quantitative_unit=draft.quantitative_unit,
                        source_chunk_id=chunk.chunk_id,
                        citation=chunk.citation,
                        domain_tags=draft.domain_tags,
                        template_tags=draft.template_tags,
                        source_quality=quality_by_id[chunk.chunk_id],
                        processor_version=self.processor_version,
                    )
                )

        if failed:
            gaps.append(
                f"{len(failed)}/{len(batches)} fact extraction batches failed; affected evidence was not interpreted."
            )
        report = FactExtractionReport(
            batch_count=len(batches),
            successful_batch_count=len(batches) - len(failed),
            failed_batch_count=len(failed),
            input_chunk_count=len(chunks),
            successful_chunk_count=sum(len(item.chunks) for item in results if item.output is not None),
            failed_chunk_count=sum(len(item.chunks) for item in failed),
            fact_count=len(valid_facts),
            batch_size=FACT_EXTRACTION_BATCH_SIZE,
            max_concurrency=FACT_EXTRACTION_MAX_CONCURRENCY,
            error_categories={
                **error_categories,
                **({"invalid_batch_chunk_reference": len(rejected_evidence)} if rejected_evidence else {}),
                **({"unregistered_fact_type": sum(rejected_fact_types.values())} if rejected_fact_types else {}),
            },
            rejected_evidence_reference_count=len(rejected_evidence),
            rejected_tag_count=len(rejected_tags),
            rejected_fact_type_count=sum(rejected_fact_types.values()),
            rejected_fact_type_distribution=dict(sorted(rejected_fact_types.items())),
        )
        self._save_trace(report, failed[0].index + 1 if failed else None)
        if not report.successful_batch_count:
            category = failed[0].category if failed else "invalid_response"
            raise StructuredModelError(
                category or "invalid_response",
                "All technology fact extraction batches failed",
                diagnostics=[
                    {"batch": item.index + 1, "category": item.category or "unexpected_error"}
                    for item in failed
                ],
            )
        return valid_facts, list(dict.fromkeys(gaps)), rejected_evidence, rejected_tags, report

    def _save_trace(self, report: FactExtractionReport, current_batch: int | None = None) -> None:
        self.last_report = report
        self.last_execution_trace = {
            "fact_extraction_batch_count": report.batch_count,
            "fact_extraction_completed_batches": report.successful_batch_count,
            "fact_extraction_failed_batches": report.failed_batch_count,
            "fact_extraction_current_batch": current_batch,
            "fact_extraction_input_chunks": report.input_chunk_count,
            "fact_extraction_successful_chunks": report.successful_chunk_count,
            "fact_extraction_failed_chunks": report.failed_chunk_count,
            "fact_extraction_fact_count": report.fact_count,
            "fact_extraction_batch_size": report.batch_size,
            "fact_extraction_max_concurrency": report.max_concurrency,
            "fact_extraction_error_categories": report.error_categories,
            "fact_extraction_rejected_evidence_references": report.rejected_evidence_reference_count,
            "fact_extraction_rejected_tags": report.rejected_tag_count,
            "fact_extraction_rejected_fact_type_count": report.rejected_fact_type_count,
            "fact_extraction_rejected_fact_type_distribution": report.rejected_fact_type_distribution,
        }


def source_quality(source: Source, chunk: KnowledgeChunk, version: SourceVersion) -> SourceQuality:
    scope = str(chunk.metadata.get("content_scope") or version.metadata.get("content_scope") or "full_content")
    if scope == "search_snippet":
        category = "snippet_only"
        rationale = "仅搜索摘要，证据等级最低，不作强里程碑依据。"
    elif source.source_type.value in {"government", "regulatory", "registry", "standard", "exchange_disclosure"}:
        category = "authoritative_public_record"
        rationale = (
            "来源属于证券交易所/法定披露平台；仍须核对其与事实的对应关系。"
            if source.source_type.value == "exchange_disclosure"
            else "根据来源类型确定为公开记录类来源；仍须核对其与事实的对应关系。"
        )
    elif source.source_type.value == "company_official":
        category = "first_party"
        rationale = "来源类型标记为企业一方来源。"
    elif source.source_type.value in {"paper", "patent"}:
        category = "academic_or_patent"
        rationale = "来源类型标记为论文或专利。"
    elif source.source_type.value == "news":
        category = "third_party"
        rationale = "来源类型标记为新闻媒体。"
    elif source.source_type.value == "web":
        category = "weak_web"
        rationale = "通用网页来源尚无可验证的权威等级元数据。"
    else:
        category = "weak_web"
        rationale = "未分类来源按较弱网页证据处理。"
    return SourceQuality(category=category, source_type=source.source_type.value, content_scope=scope, rationale=rationale)


def stable_fact_id(company_id: str, source_chunk_id: str, atom: dict, processor_version: str, template_registry_version: str) -> str:
    payload = json.dumps(
        [company_id, source_chunk_id, atom, processor_version, template_registry_version],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tf_{digest}"


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "prompts" / name
