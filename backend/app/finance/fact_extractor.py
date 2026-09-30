"""Extract source-grounded financial facts in bounded, independently auditable batches."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.finance.contracts import (
    FinancialFact,
    FinancialFactBatchOutput,
    FinancialFactExtractionReport,
)
from app.finance.registry import FinanceRegistry
from app.knowledge.contracts import KnowledgeChunk, Source, SourceVersion
from app.knowledge.semantic.extractor import source_quality
from app.knowledge.semantic.structured_call import complete_contract
from app.llm import StructuredJSONModel, StructuredModelError


FINANCIAL_FACT_BATCH_SIZE = 4
FINANCIAL_FACT_MAX_CONCURRENCY = 2


class FinancialFactExtractor:
    def __init__(
        self,
        model: StructuredJSONModel,
        registry: FinanceRegistry,
        processor_version: str = "technology-finance.v4",
        prompt_path: str | Path | None = None,
    ):
        self.model, self.registry, self.processor_version = model, registry, processor_version
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "financial_fact_extractor_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")
        self.last_report = FinancialFactExtractionReport(
            batch_size=FINANCIAL_FACT_BATCH_SIZE,
            max_concurrency=FINANCIAL_FACT_MAX_CONCURRENCY,
        )
        self.last_execution_trace: dict[str, object] = {}

    async def extract(
        self,
        company_id: str,
        chunks: list[KnowledgeChunk],
        sources: dict[str, Source],
        versions: dict[str, SourceVersion],
    ) -> tuple[list[FinancialFact], list[str], list[str]]:
        qualities = {
            chunk.chunk_id: source_quality(
                sources[chunk.source_id],
                chunk,
                versions[chunk.source_version_id or ""],
            )
            for chunk in chunks
        }
        batches = [
            chunks[index : index + FINANCIAL_FACT_BATCH_SIZE]
            for index in range(0, len(chunks), FINANCIAL_FACT_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(FINANCIAL_FACT_MAX_CONCURRENCY)
        dimensions = [
            item.model_dump(mode="json")
            for item in self.registry.innovation_dimensions + self.registry.due_diligence_dimensions
        ]

        async def extract_batch(
            batch: list[KnowledgeChunk],
        ) -> tuple[FinancialFactBatchOutput | None, dict[str, KnowledgeChunk], str | None]:
            ref_map = {f"C{index + 1}": chunk for index, chunk in enumerate(batch)}
            payload = {
                "company_id": company_id,
                "standard_dimensions": dimensions,
                "chunks": [
                    {
                        "chunk_ref": ref,
                        "text": chunk.text[:6000],
                        "source_quality": qualities[chunk.chunk_id].model_dump(mode="json"),
                    }
                    for ref, chunk in ref_map.items()
                ],
            }
            async with semaphore:
                try:
                    output = await complete_contract(
                        self.model,
                        self.prompt,
                        payload,
                        FinancialFactBatchOutput,
                        stage="financial fact extractor",
                    )
                    return output, ref_map, None
                except StructuredModelError as exc:
                    return None, ref_map, exc.category

        batch_results = await asyncio.gather(*(extract_batch(batch) for batch in batches))
        successful_batches = [result for result in batch_results if result[0] is not None]
        failed_batches = [result for result in batch_results if result[0] is None]
        error_categories = Counter(result[2] for result in failed_batches)

        facts: list[FinancialFact] = []
        gaps: list[str] = []
        rejected_refs: list[str] = []
        rejected_dimensions: Counter[str] = Counter()
        for output, ref_map, _category in batch_results:
            if output is None:
                continue
            gaps.extend(output.information_gaps)
            for draft in output.facts:
                chunk = ref_map.get(draft.source_ref)
                if chunk is None:
                    rejected_refs.append(draft.source_ref)
                    continue
                if draft.financial_dimension not in self.registry.dimension_by_id:
                    rejected_dimensions[draft.financial_dimension] += 1
                    continue
                canonical_fields = draft.model_dump(mode="json", exclude={"source_ref"})
                material = [
                    company_id,
                    chunk.chunk_id,
                    canonical_fields,
                    self.processor_version,
                    self.registry.registry_version,
                ]
                digest = hashlib.sha256(
                    json.dumps(
                        material,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()[:32]
                facts.append(
                    FinancialFact(
                        fact_id=f"ff_{digest}",
                        company_id=company_id,
                        **canonical_fields,
                        source_chunk_id=chunk.chunk_id,
                        citation=chunk.citation,
                        source_quality=qualities[chunk.chunk_id],
                        processor_version=self.processor_version,
                    )
                )

        unique_facts = list({fact.fact_id: fact for fact in facts}.values())
        if failed_batches:
            gaps.append(
                f"{len(failed_batches)}/{len(batches)} financial fact extraction batches failed; "
                "affected evidence was not interpreted."
            )
        source_quality_distribution = Counter(
            fact.source_quality.category for fact in unique_facts
        )
        dimension_distribution = Counter(
            fact.financial_dimension for fact in unique_facts
        )
        self.last_report = FinancialFactExtractionReport(
            batch_count=len(batches),
            successful_batch_count=len(successful_batches),
            failed_batch_count=len(failed_batches),
            input_chunk_count=len(chunks),
            successful_chunk_count=sum(len(result[1]) for result in successful_batches),
            failed_chunk_count=sum(len(result[1]) for result in failed_batches),
            fact_count=len(unique_facts),
            batch_size=FINANCIAL_FACT_BATCH_SIZE,
            max_concurrency=FINANCIAL_FACT_MAX_CONCURRENCY,
            error_categories=dict(sorted(error_categories.items())),
            rejected_evidence_reference_count=len(rejected_refs),
            rejected_dimension_count=sum(rejected_dimensions.values()),
            rejected_dimension_distribution=dict(sorted(rejected_dimensions.items())),
            financial_dimension_distribution=dict(sorted(dimension_distribution.items())),
            source_quality_distribution=dict(sorted(source_quality_distribution.items())),
        )
        self.last_execution_trace = self._trace(self.last_report)

        if batches and not successful_batches:
            failure_category = sorted(
                error_categories,
                key=lambda category: (-error_categories[category], category),
            )[0]
            raise StructuredModelError(
                failure_category,
                "All financial fact extraction batches failed; no facts were admitted.",
                diagnostics=[
                    {
                        "attempt": 1,
                        "location": "financial_fact_batches",
                        "type": failure_category,
                        "message": "All financial fact extraction batches failed; no facts were admitted.",
                    }
                ],
            )

        return unique_facts, gaps, rejected_refs

    @staticmethod
    def _trace(report: FinancialFactExtractionReport) -> dict[str, object]:
        return {
            "financial_fact_batch_count": report.batch_count,
            "financial_fact_completed_batches": report.successful_batch_count,
            "financial_fact_failed_batches": report.failed_batch_count,
            "financial_fact_input_chunks": report.input_chunk_count,
            "financial_fact_successful_chunks": report.successful_chunk_count,
            "financial_fact_failed_chunks": report.failed_chunk_count,
            "financial_fact_count": report.fact_count,
            "financial_fact_error_categories": dict(report.error_categories),
            "financial_fact_rejected_refs": report.rejected_evidence_reference_count,
            "financial_fact_rejected_dimensions": report.rejected_dimension_count,
            "financial_fact_rejected_dimension_distribution": dict(
                report.rejected_dimension_distribution
            ),
            "financial_dimension_distribution": dict(
                report.financial_dimension_distribution
            ),
            "financial_source_quality_distribution": dict(
                report.source_quality_distribution
            ),
        }
