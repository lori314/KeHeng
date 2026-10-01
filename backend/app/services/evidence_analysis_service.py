"""Production orchestration for the V2 evidence-first company workflow."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import inspect
import re
import threading
import unicodedata
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings, get_settings
from app.finance.processor import TechnologyFinanceProcessor
from app.knowledge.assertions import EvidenceAssertionProcessor
from app.knowledge.identity import company_for_name
from app.knowledge.semantic.processor import TechnologyKnowledgeProcessor
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.llm import OpenAICompatibleStructuredModel
from app.report_v2 import EvidenceFirstReport, EvidenceFirstReportAssembler
from app.research.orchestrator import IterativeResearchService
from app.research.planner import RetrievalPlanner
from app.research.tavily_provider import TavilySearchProvider

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class EvidenceAnalysisConfig:
    max_rounds: int = 3
    max_queries_per_round: int = 5
    max_results_per_query: int = 5


class ResearchSummary(StrictModel):
    rounds: int
    sources_found: int
    sources_ingested: int
    knowledge_chunks_added: int
    stop_reason: str | None
    entity_resolution_status: str
    official_website: str | None


class EvidenceAnalysisResult(StrictModel):
    enterprise_name: str
    status: Literal["completed", "entity_not_resolved"]
    result_status: Literal["completed", "entity_not_resolved"]
    company_id: str | None
    canonical_name: str | None
    research: ResearchSummary
    technology_profile_id: str | None
    finance_profile_id: str | None
    assertion_profile_id: str | None
    report: EvidenceFirstReport | None

class EvidenceAnalysisServiceError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        category: str | None,
        stage: str,
        message: str,
        missing_providers: list[str] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.category = category
        self.stage = stage
        self.message = message
        self.missing_providers = list(missing_providers or [])
        self.diagnostics = diagnostics or []


class EvidenceAnalysisService:
    """Run the fixed V2 pipeline against one shared, persistent knowledge base."""

    _locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
    _locks_guard = threading.Lock()
    _stage_messages = {
        "research": "公开资料检索未完成。",
        "technology_semantic": "技术证据处理未完成。",
        "technology_finance": "科技金融分析未完成。",
        "evidence_assertions": "证据断言处理未完成。",
        "evidence_first_report": "证据报告组装未完成。",
    }

    def __init__(
        self,
        runtime_root: str | Path | None = None,
        *,
        settings: Settings | None = None,
        config: EvidenceAnalysisConfig | None = None,
        model=None,
        search_provider=None,
        knowledge_base_factory=SharedKnowledgeBase,
        research_service_factory=IterativeResearchService,
        semantic_processor_factory=TechnologyKnowledgeProcessor,
        finance_processor_factory=TechnologyFinanceProcessor,
        assertion_processor_factory=EvidenceAssertionProcessor,
        report_assembler_factory=EvidenceFirstReportAssembler,
    ) -> None:
        self.settings = settings or get_settings()
        self.runtime_root = Path(runtime_root) if runtime_root else Path(__file__).resolve().parents[3] / "runtime" / "knowledge"
        self.config = config or EvidenceAnalysisConfig()
        self._model = model
        self._search_provider = search_provider
        self._knowledge_base_factory = knowledge_base_factory
        self._research_service_factory = research_service_factory
        self._semantic_processor_factory = semantic_processor_factory
        self._finance_processor_factory = finance_processor_factory
        self._assertion_processor_factory = assertion_processor_factory
        self._report_assembler_factory = report_assembler_factory
        self._execution_details_context: ContextVar[dict[str, Any] | None] = ContextVar(
            f"evidence-analysis-details-{id(self)}", default=None
        )

    @property
    def last_execution_details(self) -> dict[str, Any]:
        return self._execution_details_context.get() or {}

    @last_execution_details.setter
    def last_execution_details(self, value: dict[str, Any]) -> None:
        self._execution_details_context.set(value)

    def missing_provider_categories(self) -> list[str]:
        missing = []
        if self._model is None and not (
            self.settings.llm_endpoint and self.settings.llm_model and self.settings.llm_api_key
        ):
            missing.append("LLM")
        if self._search_provider is None and not (
            self.settings.web_search_provider == "tavily" and self.settings.tavily_api_key
        ):
            missing.append("Tavily")
        return missing

    def require_providers(self) -> None:
        missing = self.missing_provider_categories()
        if missing:
            raise EvidenceAnalysisServiceError(
                code="provider_not_configured", category=None, stage="research",
                message="真实研究所需的服务配置不完整。", missing_providers=missing,
            )

    async def analyze_company(
        self,
        enterprise_name: str,
        *,
        progress_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> EvidenceAnalysisResult:
        normalized_name = " ".join(unicodedata.normalize("NFKC", enterprise_name).split())
        if not 1 <= len(normalized_name) <= 300:
            raise EvidenceAnalysisServiceError(
                code="blank_enterprise_name" if not normalized_name else "invalid_enterprise_name",
                category="validation", stage="queued", message="企业名称长度须为 1 至 300 个字符。",
            )
        self.require_providers()
        company_seed = company_for_name(normalized_name)
        company_key = company_seed.company_id or normalized_name.casefold()
        lock = self._company_lock(company_key)
        async with lock:
            return await self._run_locked(normalized_name, company_key, progress_callback)

    async def _run_locked(self, enterprise_name, company_key, progress_callback):
        self.last_execution_details = {"stage": "queued"}
        knowledge_base = None
        stage = "research"
        try:
            knowledge_base = self._knowledge_base_factory(self.runtime_root)
            model = self._model or OpenAICompatibleStructuredModel(
                self.settings.llm_endpoint,
                self.settings.llm_model,
                self.settings.llm_api_key,
                timeout=self.settings.llm_timeout_seconds,
                enable_thinking=self.settings.llm_enable_thinking,
            )
            search = self._search_provider or TavilySearchProvider(
                self.settings.tavily_api_key,
                search_depth=self.settings.tavily_search_depth,
                timeout=self.settings.web_search_timeout_seconds,
            )
            stage = "research"
            await self._notify(progress_callback, stage, {})
            research_service = self._research_service_factory(
                RetrievalPlanner(model), search, knowledge_base
            )
            self.last_execution_details.update({"stage": stage, "research_service": research_service, "knowledge_base": knowledge_base})
            research_result = await research_service.run(
                enterprise_name,
                max_rounds=self.config.max_rounds,
                max_queries_per_round=self.config.max_queries_per_round,
                max_results_per_query=self.config.max_results_per_query,
            )
            research_trace = dict(research_service.last_execution_trace)
            self.last_execution_details.update({"research_result": research_result, "research_trace": research_trace})
            await self._notify(progress_callback, stage, {
                "round_count": research_result.rounds,
                "source_count": research_result.sources_ingested,
            })
            company = knowledge_base.repository.get_company(company_key)
            self.last_execution_details["company"] = company
            summary = ResearchSummary(
                rounds=research_result.rounds,
                sources_found=research_result.sources_found,
                sources_ingested=research_result.sources_ingested,
                knowledge_chunks_added=research_result.knowledge_chunks_added,
                stop_reason=research_result.stop_reason,
                entity_resolution_status=research_result.entity_resolution_status,
                official_website=research_result.official_website or getattr(company, "official_website", None),
            )
            if research_result.entity_resolution_status != "resolved" or company is None:
                return EvidenceAnalysisResult(
                    enterprise_name=enterprise_name, status="completed", result_status="entity_not_resolved",
                    company_id=getattr(company, "company_id", None), canonical_name=getattr(company, "canonical_name", None),
                    research=summary, technology_profile_id=None, finance_profile_id=None,
                    assertion_profile_id=None, report=None,
                )

            company_id = company.company_id
            stage = "technology_semantic"
            await self._notify(progress_callback, stage, {})
            semantic_processor = self._semantic_processor_factory(model, knowledge_base)
            self.last_execution_details.update({"stage": stage, "semantic_processor": semantic_processor})
            technology_profile = await semantic_processor.process_company(company_id)
            self.last_execution_details.update({"technology_profile": technology_profile, "semantic_trace": dict(semantic_processor.last_execution_trace)})
            await self._notify(progress_callback, stage, {"fact_count": len(technology_profile.technology_facts), "profile_id": technology_profile.profile_id})

            stage = "technology_finance"
            await self._notify(progress_callback, stage, {})
            finance_processor = self._finance_processor_factory(model, knowledge_base)
            self.last_execution_details.update({"stage": stage, "finance_processor": finance_processor})
            finance_profile = await finance_processor.process_company(company_id)
            self.last_execution_details.update({"finance_profile": finance_profile, "finance_trace": dict(finance_processor.last_execution_trace)})
            await self._notify(progress_callback, stage, {"fact_count": len(finance_profile.financial_facts), "profile_id": finance_profile.profile_id})

            stage = "evidence_assertions"
            await self._notify(progress_callback, stage, {})
            assertion_processor = self._assertion_processor_factory(knowledge_base)
            self.last_execution_details.update({"stage": stage, "assertion_processor": assertion_processor})
            assertion_profile = assertion_processor.process_company(company_id)
            self.last_execution_details.update({"assertion_profile": assertion_profile, "assertion_trace": dict(assertion_processor.last_execution_trace)})
            await self._notify(progress_callback, stage, {"fact_count": len(assertion_profile.assertions), "profile_id": assertion_profile.profile_id})

            stage = "evidence_first_report"
            await self._notify(progress_callback, stage, {})
            report = self._report_assembler_factory(knowledge_base.repository).build(company_id)
            if report.company_id != company_id:
                raise ValueError("report_company_id_mismatch")
            self.last_execution_details.update({"stage": stage, "report": report})
            await self._notify(progress_callback, "complete", {"profile_id": report.report_id})
            return EvidenceAnalysisResult(
                enterprise_name=enterprise_name, status="completed", result_status="completed",
                company_id=company_id, canonical_name=company.canonical_name, research=summary,
                technology_profile_id=technology_profile.profile_id,
                finance_profile_id=finance_profile.profile_id,
                assertion_profile_id=assertion_profile.profile_id, report=report,
            )
        except EvidenceAnalysisServiceError:
            raise
        except Exception as exc:
            stage = str(self.last_execution_details.get("stage", stage))
            category = getattr(exc, "category", type(exc).__name__)
            category = re.sub(r"[^A-Za-z0-9_.-]", "_", str(category))[:80] or "unexpected_error"
            diagnostics = getattr(exc, "diagnostics", None)
            safe_diagnostics = []
            if isinstance(diagnostics, list):
                for item in diagnostics:
                    if isinstance(item, dict):
                        safe_diagnostics.append({k: v for k, v in item.items() if k in {"attempt", "location", "type", "message"} and isinstance(v, (str, int))})
            code = f"{stage}_failed"
            raise EvidenceAnalysisServiceError(
                code=code, category=category, stage=stage,
                message=self._stage_messages.get(stage, "分析流程执行失败。"),
                diagnostics=safe_diagnostics,
            ) from exc
        finally:
            self.last_execution_details["stage"] = stage
            if knowledge_base is not None:
                knowledge_base.close()

    @classmethod
    def _company_lock(cls, company_key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with cls._locks_guard:
            loop_locks = cls._locks.get(loop)
            if loop_locks is None:
                loop_locks = {}
                cls._locks[loop] = loop_locks
            lock = loop_locks.get(company_key)
            if lock is None:
                lock = asyncio.Lock()
                loop_locks[company_key] = lock
            return lock

    @staticmethod
    async def _notify(callback, stage, metadata):
        if callback is None:
            return
        safe = {key: value for key, value in metadata.items() if key in {"round_count", "source_count", "fact_count", "profile_id"} and isinstance(value, (int, float, str))}
        result = callback(stage, safe)
        if inspect.isawaitable(result):
            await result
