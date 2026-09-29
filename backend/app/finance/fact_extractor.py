"""Extract atomic, one-source financial and operating facts from current chunks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.finance.contracts import FinancialFact, FinancialFactExtraction
from app.finance.registry import FinanceRegistry
from app.knowledge.contracts import KnowledgeChunk, Source, SourceVersion
from app.knowledge.semantic.extractor import source_quality
from app.knowledge.semantic.structured_call import complete_contract
from app.llm import StructuredJSONModel


class FinancialFactExtractor:
    def __init__(self, model: StructuredJSONModel, registry: FinanceRegistry, processor_version: str = "technology-finance.v1", prompt_path: str | Path | None = None):
        self.model, self.registry, self.processor_version = model, registry, processor_version
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "financial_fact_extractor_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")

    async def extract(self, company_id: str, chunks: list[KnowledgeChunk], sources: dict[str, Source], versions: dict[str, SourceVersion]) -> tuple[list[FinancialFact], list[str], list[str]]:
        if not chunks:
            return [], [], []
        contexts = []
        quality = {}
        for chunk in chunks:
            source, version = sources[chunk.source_id], versions[chunk.source_version_id or ""]
            quality[chunk.chunk_id] = source_quality(source, chunk, version)
            contexts.append({"chunk_id": chunk.chunk_id, "text": chunk.text[:6000], "citation": chunk.citation.model_dump(mode="json"), "source_quality": quality[chunk.chunk_id].model_dump(mode="json")})
        output = await complete_contract(self.model, self.prompt, {"company_id": company_id, "standard_dimensions": [x.model_dump(mode="json") for x in self.registry.innovation_dimensions + self.registry.due_diligence_dimensions], "chunks": contexts}, FinancialFactExtraction, stage="financial fact extractor")
        by_id = {x.chunk_id: x for x in chunks}
        facts, rejected = [], []
        for draft in output.facts:
            chunk = by_id.get(draft.source_chunk_id)
            if chunk is None or draft.financial_dimension not in self.registry.dimension_by_id:
                rejected.append(draft.source_chunk_id)
                continue
            material = [company_id, chunk.chunk_id, draft.model_dump(mode="json"), self.processor_version, self.registry.registry_version]
            digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
            facts.append(FinancialFact(fact_id=f"ff_{digest}", company_id=company_id, **draft.model_dump(), citation=chunk.citation, source_quality=quality[chunk.chunk_id], processor_version=self.processor_version))
        unique = {x.fact_id: x for x in facts}
        return list(unique.values()), output.information_gaps, rejected
