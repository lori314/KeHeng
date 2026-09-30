"""End-to-end current knowledge → financial facts → bounded joint reasoning."""

from app.finance.fact_extractor import FinancialFactExtractor
from app.finance.mapper import TechnologyFinanceMapper
from app.finance.registry import FinanceRegistry
from app.finance.validator import TechFinanceValidator
from app.knowledge.contracts import KnowledgeLayer, Source
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.llm import StructuredJSONModel


PROCESSOR_VERSION = "technology-finance.v4"
MAX_FINANCE_CHUNKS_PER_PROCESS = 60


class TechnologyFinanceProcessor:
    def __init__(self, model: StructuredJSONModel, knowledge_base: SharedKnowledgeBase, registry: FinanceRegistry | None = None, processor_version: str = PROCESSOR_VERSION):
        self.model, self.knowledge_base = model, knowledge_base
        self.repository = knowledge_base.repository
        self.registry = registry or FinanceRegistry()
        self.processor_version = processor_version
        self.extractor = FinancialFactExtractor(model, self.registry, processor_version)
        self.mapper = TechnologyFinanceMapper(self.registry)
        self.validator = TechFinanceValidator(self.registry)
        self.last_execution_trace: dict[str, object] = {
            "finance_stage": "not_started",
            "finance_substage": "not_started",
        }

    async def process_company(self, company_id: str) -> object:
        active_stage = "finance_input_preparation"
        self.last_execution_trace = {
            "finance_stage": active_stage,
            "finance_substage": active_stage,
            "processor_version": self.processor_version,
        }
        try:
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

            active_stage = "financial_fact_extraction"
            self.last_execution_trace.update({
                "finance_stage": active_stage,
                "finance_substage": active_stage,
                "financial_fact_input_chunk_count": len(chunks),
            })
            facts, extraction_gaps, rejected = await self.extractor.extract(
                company_id, chunks, sources, versions
            )
            self.last_execution_trace.update(self.extractor.last_execution_trace)
            self.last_execution_trace["financial_fact_count"] = len(facts)

            active_stage = "technology_finance_mapping"
            self.last_execution_trace.update({
                "finance_stage": active_stage,
                "finance_substage": active_stage,
            })
            profile = await self.mapper.map(
                technology,
                facts,
                extraction_gaps + ([f"未接受引用不存在输入 chunk 的金融事实数：{len(rejected)}"] if rejected else []),
                self.processor_version,
            )
            self.last_execution_trace.update({
                "mapping_mode": self.mapper.last_execution_trace.get("mapping_mode"),
                "finance_mapping_status": self.mapper.last_execution_trace.get("mapping_status"),
                "finance_candidate_rules": self.mapper.last_execution_trace.get("candidate_rule_ids", []),
                "finance_candidate_rule_count": self.mapper.last_execution_trace.get("candidate_rule_count", 0),
                "finance_milestone_conditioned_candidate_count": self.mapper.last_execution_trace.get("milestone_conditioned_candidate_count", 0),
                "finance_general_candidate_count": self.mapper.last_execution_trace.get("general_candidate_count", 0),
                "finance_applied_rules": profile.applicable_rule_ids,
                "funding_activity_count": len(profile.funding_activities),
                "risk_observation_count": len(profile.risk_observations),
                "monitoring_node_count": len(profile.monitoring_nodes),
            })
            profile = profile.model_copy(update={
                "financial_fact_extraction_report": self.extractor.last_report,
            })

            active_stage = "finance_validation"
            self.last_execution_trace.update({
                "finance_stage": active_stage,
                "finance_substage": active_stage,
            })
            profile = self.validator.validate(profile, technology)

            active_stage = "finance_profile_persistence"
            self.last_execution_trace.update({
                "finance_stage": active_stage,
                "finance_substage": active_stage,
            })
            self.repository.save_technology_finance_profile(profile)
            self.last_execution_trace.update({
                "finance_stage": "complete",
                "finance_substage": "complete",
                "status": "completed",
            })
            return profile
        except Exception as exc:
            self.last_execution_trace.update({
                "finance_stage": "failed",
                "finance_substage": active_stage,
                "failed_stage": active_stage,
                "status": "failed",
                "error_category": getattr(exc, "category", type(exc).__name__),
            })
            self.last_execution_trace.update(self.extractor.last_execution_trace)
            self.last_execution_trace.update({
                "mapping_mode": self.mapper.last_execution_trace.get("mapping_mode"),
                "finance_mapping_status": self.mapper.last_execution_trace.get("mapping_status"),
                "finance_candidate_rules": self.mapper.last_execution_trace.get("candidate_rule_ids", []),
                "finance_candidate_rule_count": self.mapper.last_execution_trace.get("candidate_rule_count", 0),
                "finance_milestone_conditioned_candidate_count": self.mapper.last_execution_trace.get("milestone_conditioned_candidate_count", 0),
                "finance_general_candidate_count": self.mapper.last_execution_trace.get("general_candidate_count", 0),
                "finance_selected_rules": self.mapper.last_execution_trace.get("selected_rule_ids", []),
                "finance_applied_rules": [],
            })
            raise
