"""Evidence-bound Industry Agent for market and policy observations.

This v0.7 implementation is deterministic and local. It only turns retrieved
enterprise material into four industry indicator observations; it does not use
industry common sense to invent facts or calculate a weighted enterprise value.
"""

from __future__ import annotations

from collections import OrderedDict
from enum import StrEnum
import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from app.llm import (
    LLMContextChunk,
    LLMExtractionRequest,
    LLMIndustryExtractionResult,
    LLMProvider,
    LLMProviderError,
)
from app.rag.retrieval import RetrievalQuery, RetrievedEvidence, Retriever
from app.rag.query_definitions import domain_queries
from app.rag.context_assembly import assemble_context


class IndustryAgentRequest(BaseModel):
    """Request scoped to one task's retrieved evidence."""

    task_id: str = Field(min_length=1)
    enterprise_name: str = Field(default="未命名企业", min_length=1, max_length=120)
    top_k_per_query: int = Field(default=3, ge=1, le=10)
    evidence_id_start: int = Field(default=1, ge=1, le=10000)


class IndustryAgentMode(StrEnum):
    RULE = "rule"
    LLM = "llm"


class IndustryIndicatorAssessment(BaseModel):
    """One industry observation; score is an input to the industry engine."""

    model_config = ConfigDict(extra="forbid")

    score: int | None = Field(default=None, ge=0, le=100)
    evidence: list[str] = Field(default_factory=list)
    rationale: str


class IndustryIndicators(BaseModel):
    market_potential: IndustryIndicatorAssessment
    industry_growth: IndustryIndicatorAssessment
    competitive_position: IndustryIndicatorAssessment
    policy_environment: IndustryIndicatorAssessment


class IndustryEvidence(BaseModel):
    evidence_id: str
    document_name: str
    page_number: int | None
    chunk_id: str
    excerpt: str
    retrieval_score: float
    supports: list[str]


class IndustryAnalysis(BaseModel):
    """Fixed industry output plus source metadata for report traceability."""

    model_config = ConfigDict(extra="forbid")

    industry_indicators: IndustryIndicators
    evidence: list[IndustryEvidence]


EvidenceReference = Callable[[RetrievedEvidence, str], str]
INDUSTRY_INDICATORS = (
    "market_potential",
    "industry_growth",
    "competitive_position",
    "policy_environment",
)


class IndustryAgent:
    """Conservative rule-based extraction of industry-specific enterprise facts."""

    name = "industry_agent"
    implementation_version = "local-industry-rules-v0.7"

    _queries = (
        "行业 市场规模 市场需求 应用场景 客户 订单 商业化",
        "行业增长 增速 CAGR 行业趋势 需求增长",
        "竞争 对手 市场份额 差异化 壁垒 行业领先 核心客户",
        "政策 产业规划 补贴 专项资金 支持 监管 合规",
    )

    def __init__(
        self,
        retriever: Retriever,
        mode: IndustryAgentMode | str = IndustryAgentMode.RULE,
        llm_provider: LLMProvider | None = None,
        query_mode: str = "original",
    ) -> None:
        self._retriever = retriever
        self._mode = IndustryAgentMode(mode)
        self._llm_provider = llm_provider
        self._queries = domain_queries("industry") if query_mode == "bilingual" else type(self)._queries
        self._prompt = self._load_prompt()
        self.last_context_assembly: dict[str, object] = {}

    async def analyze(self, request: IndustryAgentRequest) -> IndustryAnalysis:
        evidence_items = await self._retrieve(request)
        if self._mode is IndustryAgentMode.LLM:
            if not evidence_items:
                return IndustryAnalysis(industry_indicators=self._empty_indicators(), evidence=[])
            evidence_items, self.last_context_assembly = assemble_context(evidence_items)
            return await self._analyze_with_llm(request, evidence_items)
        evidence_ids = {
            item.chunk_id: f"E{index}"
            for index, item in enumerate(
                evidence_items, start=request.evidence_id_start
            )
        }
        supports: dict[str, set[str]] = {
            evidence_id: set() for evidence_id in evidence_ids.values()
        }

        def reference(item: RetrievedEvidence, field_path: str) -> str:
            evidence_id = evidence_ids[item.chunk_id]
            supports[evidence_id].add(field_path)
            return evidence_id

        if not evidence_items:
            indicators = self._empty_indicators()
        else:
            indicators = IndustryIndicators(
                market_potential=self._market_potential(evidence_items, reference),
                industry_growth=self._industry_growth(evidence_items, reference),
                competitive_position=self._competitive_position(
                    evidence_items, reference
                ),
                policy_environment=self._policy_environment(
                    evidence_items, reference
                ),
            )

        evidence = [
            IndustryEvidence(
                evidence_id=evidence_ids[item.chunk_id],
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                excerpt=item.text,
                retrieval_score=round(item.score, 4),
                supports=sorted(supports[evidence_ids[item.chunk_id]]),
            )
            for item in evidence_items
            if supports[evidence_ids[item.chunk_id]]
        ]
        return IndustryAnalysis(industry_indicators=indicators, evidence=evidence)

    async def _analyze_with_llm(
        self, request: IndustryAgentRequest, evidence_items: list[RetrievedEvidence]
    ) -> IndustryAnalysis:
        if self._llm_provider is None:
            raise LLMProviderError(
                "Industry LLM extraction requires an injected provider",
                category="provider_not_configured",
            )
        evidence_ids = {
            item.chunk_id: f"E{index}"
            for index, item in enumerate(evidence_items, start=request.evidence_id_start)
        }
        context = [
            LLMContextChunk(
                evidence_id=evidence_ids[item.chunk_id],
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                excerpt=item.text,
                retrieval_score=round(item.score, 4),
            )
            for item in evidence_items
        ]
        result = await self._llm_provider.extract_industry(
            LLMExtractionRequest(
                task_id=request.task_id,
                enterprise_name=request.enterprise_name,
                chunks=context,
                prompt=self._prompt,
                context_assembly=self.last_context_assembly,
            )
        )
        return self._convert_llm_result(result, evidence_items, evidence_ids)

    def _convert_llm_result(
        self,
        result: LLMIndustryExtractionResult,
        evidence_items: list[RetrievedEvidence],
        evidence_ids: dict[str, str],
    ) -> IndustryAnalysis:
        expected = set(INDUSTRY_INDICATORS)
        unknown = set(result.industry_indicators) - expected
        if unknown:
            raise ValueError(f"Industry LLM returned unknown indicators: {sorted(unknown)}")
        valid_ids = set(evidence_ids.values())
        supports: dict[str, set[str]] = {item: set() for item in valid_ids}

        def checked(ids: list[str], field: str, required: bool = False) -> list[str]:
            normalized = list(dict.fromkeys(ids))
            if required and not normalized:
                raise ValueError(f"Industry LLM {field} requires evidence")
            unknown_ids = [item for item in normalized if item not in valid_ids]
            if unknown_ids:
                raise ValueError(f"Industry LLM {field} cites unknown evidence: {unknown_ids}")
            for evidence_id in normalized:
                supports[evidence_id].add(field)
            return normalized

        values: dict[str, IndustryIndicatorAssessment] = {}
        citation_pattern = re.compile(r"\[([A-Z]\d+)\]")
        for indicator_id in INDUSTRY_INDICATORS:
            extraction = result.industry_indicators.get(indicator_id)
            if extraction is None:
                values[indicator_id] = IndustryIndicatorAssessment(rationale="LLM 未返回该指标，证据不足，指标未评分。")
                continue
            ids = checked(extraction.evidence_ids, f"industry_indicators.{indicator_id}", extraction.score is not None)
            rationale_ids = set(citation_pattern.findall(extraction.rationale))
            if not rationale_ids <= set(ids):
                raise ValueError(f"Industry LLM {indicator_id} rationale cites evidence not bound to the indicator")
            values[indicator_id] = IndustryIndicatorAssessment(
                score=extraction.score, evidence=ids, rationale=extraction.rationale
            )
        evidence = [
            IndustryEvidence(
                evidence_id=evidence_ids[item.chunk_id],
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                excerpt=item.text,
                retrieval_score=round(item.score, 4),
                supports=sorted(supports[evidence_ids[item.chunk_id]]),
            )
            for item in evidence_items
            if supports[evidence_ids[item.chunk_id]]
        ]
        return IndustryAnalysis(industry_indicators=IndustryIndicators(**values), evidence=evidence)

    @staticmethod
    def _load_prompt() -> str:
        path = Path(__file__).resolve().parents[3] / "prompts" / "industry_agent_prompt.md"
        return path.read_text(encoding="utf-8") if path.is_file() else "只根据证据抽取产业指标。"

    async def _retrieve(self, request: IndustryAgentRequest) -> list[RetrievedEvidence]:
        retrieved: OrderedDict[str, RetrievedEvidence] = OrderedDict()
        for query_index, query in enumerate(self._queries):
            results = await self._retriever.search(
                RetrievalQuery(
                    task_id=request.task_id,
                    query=query,
                    top_k=request.top_k_per_query,
                )
            )
            for item in results:
                item = item.model_copy(update={"metadata": {**item.metadata, "retrieval_query_groups": str(query_index)}})
                current = retrieved.get(item.chunk_id)
                if current is None or item.score > current.score:
                    groups = sorted(set(current.metadata.get("retrieval_query_groups", "").split(",")) | {str(query_index)}) if current else [str(query_index)]
                    retrieved[item.chunk_id] = item.model_copy(update={"metadata": {**item.metadata, "retrieval_query_groups": ",".join(groups)}})
                else:
                    groups = sorted(set(current.metadata.get("retrieval_query_groups", "").split(",")) | {str(query_index)})
                    retrieved[item.chunk_id] = current.model_copy(update={"metadata": {**current.metadata, "retrieval_query_groups": ",".join(groups)}})
        return list(retrieved.values())

    @staticmethod
    def _empty_indicators() -> IndustryIndicators:
        missing = "证据不足，产业指标未评分。"
        return IndustryIndicators(
            market_potential=IndustryIndicatorAssessment(rationale=missing),
            industry_growth=IndustryIndicatorAssessment(rationale=missing),
            competitive_position=IndustryIndicatorAssessment(rationale=missing),
            policy_environment=IndustryIndicatorAssessment(rationale=missing),
        )

    def _market_potential(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> IndustryIndicatorAssessment:
        item = self._first_match(
            evidence, ("尚无客户", "没有订单", "未形成销售", "尚未商业化")
        )
        if item:
            return self._scored(
                20, item, reference,
                "材料明确披露客户、订单或商业化证据不足，市场潜力仅作低分观察。",
                "market_potential",
            )
        item = self._first_factual(evidence, ("已签约", "订单", "客户", "交付", "销售"))
        if item:
            return self._scored(
                75, item, reference,
                "材料提供了企业客户、订单或交付事实，支持较强市场需求观察。",
                "market_potential",
            )
        item = self._first_factual(evidence, ("应用场景", "市场需求", "市场规模"))
        if item:
            return self._scored(
                55, item, reference,
                "材料描述了应用场景或市场需求，但未充分证明企业商业化表现。",
                "market_potential",
            )
        return IndustryIndicatorAssessment(rationale="未检索到企业市场潜力的有效证据。")

    def _industry_growth(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> IndustryIndicatorAssessment:
        item = self._first_factual(evidence, ("下滑", "萎缩", "收缩", "负增长"))
        if item:
            return self._scored(
                20, item, reference,
                "材料描述行业需求或规模承压，行业成长性观察保持保守。",
                "industry_growth",
            )
        item = self._first_factual(
            evidence,
            ("高速增长", "快速增长", "增长率", "复合增长", "CAGR", "市场规模增长"),
        )
        if item:
            return self._scored(
                75, item, reference,
                "材料提供了行业增长或规模扩张描述，支持较高成长性观察。",
                "industry_growth",
            )
        item = self._first_factual(evidence, ("增长", "需求上升", "行业扩张"))
        if item:
            return self._scored(
                55, item, reference,
                "材料提到行业增长趋势，但增长幅度和持续性证据有限。",
                "industry_growth",
            )
        return IndustryIndicatorAssessment(rationale="未检索到行业成长性的有效证据。")

    def _competitive_position(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> IndustryIndicatorAssessment:
        item = self._first_factual(
            evidence, ("竞争激烈", "对手众多", "同质化", "市场份额下降")
        )
        if item:
            return self._scored(
                30, item, reference,
                "材料提示竞争压力或同质化风险，企业相对位置尚需进一步核验。",
                "competitive_position",
            )
        item = self._first_factual(
            evidence,
            (
                "市场份额", "行业领先", "排名第一", "核心客户", "竞争优势",
                "差异化", "技术壁垒", "进入壁垒",
            ),
        )
        if item:
            return self._scored(
                70, item, reference,
                "材料提供了企业竞争位置、客户或壁垒相关事实，但未替代独立市场核验。",
                "competitive_position",
            )
        return IndustryIndicatorAssessment(rationale="未检索到企业竞争位置的有效证据。")

    def _policy_environment(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> IndustryIndicatorAssessment:
        item = self._first_factual(
            evidence, ("监管趋严", "政策限制", "合规压力", "监管不确定")
        )
        if item:
            return self._scored(
                30, item, reference,
                "材料提示政策或监管约束，政策环境存在不确定性。",
                "policy_environment",
            )
        item = self._first_factual(
            evidence, ("政策支持", "产业规划", "专项资金", "补贴", "鼓励", "战略")
        )
        if item:
            return self._scored(
                75, item, reference,
                "材料提供了政策支持或产业规划信息，但不据此推断企业必然受益。",
                "policy_environment",
            )
        item = self._first_factual(evidence, ("政策", "监管", "合规要求"))
        if item:
            return self._scored(
                50, item, reference,
                "材料提及政策或监管环境，但具体影响方向和适用范围仍有限。",
                "policy_environment",
            )
        return IndustryIndicatorAssessment(rationale="未检索到政策环境的有效证据。")

    @staticmethod
    def _scored(
        score: int,
        item: RetrievedEvidence,
        reference: EvidenceReference,
        rationale: str,
        indicator_id: str,
    ) -> IndustryIndicatorAssessment:
        return IndustryIndicatorAssessment(
            score=score,
            evidence=[reference(item, f"industry_indicators.{indicator_id}")],
            rationale=rationale,
        )

    @staticmethod
    def _first_factual(
        evidence: list[RetrievedEvidence], keywords: tuple[str, ...]
    ) -> RetrievedEvidence | None:
        non_factual_markers = (
            "计划", "预计", "拟", "目标", "未来", "尚未", "未完成",
            "待验证", "建议", "需要", "不能", "虚构", "不对应",
            "仅用于测试", "测试材料",
        )
        for item in evidence:
            clauses = re.split(r"[。！？；]", item.text)
            for clause in clauses:
                if any(keyword in clause for keyword in keywords) and not any(
                    marker in clause for marker in non_factual_markers
                ):
                    return item
        return None

    @staticmethod
    def _first_match(
        evidence: list[RetrievedEvidence], keywords: tuple[str, ...]
    ) -> RetrievedEvidence | None:
        return next(
            (item for item in evidence if any(keyword in item.text for keyword in keywords)),
            None,
        )


def _compress_llm_evidence(evidence_items: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    """Compatibility wrapper for previous internal imports."""
    return assemble_context(evidence_items)[0]
