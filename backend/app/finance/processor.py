"""End-to-end current knowledge → financial facts → bounded joint reasoning."""

from app.finance.fact_extractor import FinancialFactExtractor
from app.finance.mapper import TechnologyFinanceMapper
from app.finance.registry import FinanceRegistry
from app.finance.validator import TechFinanceValidator
from app.knowledge.contracts import KnowledgeLayer, Source
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.llm import StructuredJSONModel


PROCESSOR_VERSION = "technology-finance.v1"
MAX_FINANCE_CHUNKS_PER_PROCESS = 60


class TechnologyFinanceProcessor:
    def __init__(self, model: StructuredJSONModel, knowledge_base: SharedKnowledgeBase, registry: FinanceRegistry | None = None, processor_version: str = PROCESSOR_VERSION):
        self.model, self.knowledge_base = model, knowledge_base
        self.repository = knowledge_base.repository
        self.registry = registry or FinanceRegistry()
        self.processor_version = processor_version
        self.extractor = FinancialFactExtractor(model, self.registry, processor_version)
        self.mapper = TechnologyFinanceMapper(model, self.registry)
        self.validator = TechFinanceValidator(self.registry)

    async def process_company(self, company_id: str) -> object:
        company = self.repository.get_company(company_id)
        if company is None:
            raise ValueError(f"Unknown company_id: {company_id}")
        raw = self.repository.get_technology_semantic_profile(company_id)
        if raw is None:
            raise ValueError("No TechnologySemanticProfile exists; run technology semantic processing first")
        technology = TechnologySemanticProfile.model_validate(raw)
        chunks = []
        for layer in (KnowledgeLayer.GENERAL.value, KnowledgeLayer.ENTERPRISE.value):
            chunks.extend(
                chunk for chunk in self.repository.list_current_chunks(company_id, layer)
                if chunk.metadata.get("semantic_kind") not in {"technology_fact", "financial_fact"}
            )
        chunks.sort(key=lambda x: (x.source_version_id or "", x.chunk_id))
        chunks = chunks[:MAX_FINANCE_CHUNKS_PER_PROCESS]
        sources: dict[str, Source] = {}
        versions = {}
        for chunk in chunks:
            source = self.repository.get_source(chunk.source_id)
            version = self.repository.get_source_version(chunk.source_version_id or "")
            if source is None or version is None:
                raise RuntimeError(f"KnowledgeChunk {chunk.chunk_id} has missing provenance")
            sources[source.source_id], versions[version.source_version_id] = source, version
        facts, extraction_gaps, rejected = await self.extractor.extract(company_id, chunks, sources, versions)
        profile = await self.mapper.map(technology, facts, extraction_gaps + ([f"未接受引用不存在输入 chunk 的事实：{x}" for x in rejected]), self.processor_version)
        profile = self.validator.validate(profile, technology)
        self.repository.save_technology_finance_profile(profile)
        return profile
