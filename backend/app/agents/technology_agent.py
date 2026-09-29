"""Evidence-bound Technology Agent indicator extraction.

The Agent extracts four indicator observations for the deterministic Evaluation
Engine. Rule extraction is the default; an injected LLM Provider can be used
for structured extraction without allowing it to calculate a final score.
"""

from collections import OrderedDict
from enum import StrEnum
import re
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.llm import (
    LLMContextChunk,
    LLMExtractionRequest,
    LLMExtractionResult,
    LLMProvider,
    LLMProviderError,
)
from app.rag.retrieval import RetrievalQuery, RetrievedEvidence, Retriever
from app.rag.query_definitions import domain_queries
from app.rag.context_assembly import assemble_context


class TechnologyAgentRequest(BaseModel):
    task_id: str = Field(min_length=1)
    enterprise_name: str = Field(default="未命名企业", min_length=1, max_length=120)
    top_k_per_query: int = Field(default=3, ge=1, le=10)


class TechnologyAgentMode(StrEnum):
    RULE = "rule"
    LLM = "llm"
    FALLBACK = "fallback"


class TechnologyEvidence(BaseModel):
    evidence_id: str
    document_name: str
    page_number: int | None
    chunk_id: str
    excerpt: str
    retrieval_score: float
    supports: list[str]


class TechnologyIndicatorAssessment(BaseModel):
    """One rubric-level observation; this is not a final weighted score."""

    score: int | None = Field(default=None, ge=0, le=100)
    evidence: list[str] = Field(default_factory=list)
    rationale: str


class TechnologyIndicators(BaseModel):
    technical_autonomy: TechnologyIndicatorAssessment
    innovation_capability: TechnologyIndicatorAssessment
    intellectual_property: TechnologyIndicatorAssessment
    technical_maturity: TechnologyIndicatorAssessment


class TechnologyAnalysis(BaseModel):
    """v0.3 Agent output: indicator observations plus original evidence."""

    model_config = ConfigDict(extra="forbid")

    technology_indicators: TechnologyIndicators
    technology_summary: str
    summary_status: Literal["supported", "insufficient_evidence"] = "supported"
    strengths: list[str]
    risks: list[str]
    evidence: list[TechnologyEvidence]


EvidenceReference = Callable[[RetrievedEvidence, str], str]


class TechnologyAgent:
    """Extract conservative technical indicators only from retrieved evidence."""

    name = "technology_agent"
    implementation_version = "local-indicator-rules-v0.6"

    _queries = (
        "核心技术 自主研发 算法 技术方案 创新 研发团队",
        "专利 知识产权 实质审查 授权 软件著作权",
        "原型 样机 测试 中试 试点 客户 部署 量产 技术成熟度",
        "技术风险 依赖 限制 尚未 待验证 规模化",
    )

    def __init__(
        self,
        retriever: Retriever,
        mode: TechnologyAgentMode | str = TechnologyAgentMode.RULE,
        llm_provider: LLMProvider | None = None,
        query_mode: str = "original",
    ) -> None:
        self._retriever = retriever
        self._mode = TechnologyAgentMode(mode)
        self._llm_provider = llm_provider
        self._queries = domain_queries("technology") if query_mode == "bilingual" else type(self)._queries
        self._prompt = self._load_prompt()
        self.last_context_assembly: dict[str, object] = {}

    async def analyze(self, request: TechnologyAgentRequest) -> TechnologyAnalysis:
        evidence_items = await self._retrieve(request)
        if not evidence_items:
            return TechnologyAnalysis(
                technology_indicators=self._empty_indicators(),
                technology_summary="证据不足：当前任务未检索到可用于技术分析的资料。",
                summary_status="insufficient_evidence",
                strengths=[],
                risks=[],
                evidence=[],
            )

        if self._mode is not TechnologyAgentMode.RULE:
            try:
                return await self._analyze_with_llm(request, evidence_items)
            except LLMProviderError:
                if self._mode is not TechnologyAgentMode.FALLBACK:
                    raise
            except ValueError as exc:
                # Normalize provider/contract validation failures so the
                # application can report an explicit LLM failure in llm mode.
                if self._mode is not TechnologyAgentMode.FALLBACK:
                    raise LLMProviderError(
                        "LLM extraction response violates its contract"
                    ) from exc

        evidence_ids = {
            item.chunk_id: f"E{index}"
            for index, item in enumerate(evidence_items, start=1)
        }
        supports: dict[str, set[str]] = {
            evidence_id: set() for evidence_id in evidence_ids.values()
        }

        def reference(item: RetrievedEvidence, field_path: str) -> str:
            evidence_id = evidence_ids[item.chunk_id]
            supports[evidence_id].add(field_path)
            return evidence_id

        summary_items = evidence_items[: min(3, len(evidence_items))]
        summary_refs = "".join(
            f"[{reference(item, 'technology_summary')}]" for item in summary_items
        )
        technology_summary = (
            "检索材料显示企业已形成技术研发、知识产权或验证活动记录；"
            "以下指标仅基于已提供资料提取，最终技术分由独立评价引擎计算。"
            f"{summary_refs}"
        )

        indicators = TechnologyIndicators(
            technical_autonomy=self._technical_autonomy(evidence_items, reference),
            innovation_capability=self._innovation_capability(
                evidence_items, reference
            ),
            intellectual_property=self._intellectual_property(
                evidence_items, reference
            ),
            technical_maturity=self._technical_maturity(evidence_items, reference),
        )
        strengths = self._strengths(evidence_items, reference)
        risks = self._risks(evidence_items, reference)

        evidence = []
        for item in evidence_items:
            evidence_id = evidence_ids[item.chunk_id]
            if not supports[evidence_id]:
                continue
            evidence.append(
                TechnologyEvidence(
                    evidence_id=evidence_id,
                    document_name=item.document_name,
                    page_number=item.page_number,
                    chunk_id=item.chunk_id,
                    excerpt=item.text,
                    retrieval_score=round(item.score, 4),
                    supports=sorted(supports[evidence_id]),
                )
            )

        return TechnologyAnalysis(
            technology_indicators=indicators,
            technology_summary=technology_summary,
            strengths=strengths,
            risks=risks,
            evidence=evidence,
        )

    async def _analyze_with_llm(
        self,
        request: TechnologyAgentRequest,
        evidence_items: list[RetrievedEvidence],
    ) -> TechnologyAnalysis:
        if self._llm_provider is None:
            raise LLMProviderError(
                "LLM extraction mode requires an injected provider"
            )
        evidence_items, self.last_context_assembly = assemble_context(evidence_items)
        evidence_ids = {
            item.chunk_id: f"E{index}"
            for index, item in enumerate(evidence_items, start=1)
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
        result = await self._llm_provider.extract(
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
        result: LLMExtractionResult,
        evidence_items: list[RetrievedEvidence],
        evidence_ids: dict[str, str],
    ) -> TechnologyAnalysis:
        valid_ids = set(evidence_ids.values())
        supports: dict[str, set[str]] = {item: set() for item in valid_ids}

        def checked_ids(ids: list[str], field_path: str, required: bool = False) -> list[str]:
            normalized = list(dict.fromkeys(ids))
            unknown = [item for item in normalized if item not in valid_ids]
            if unknown:
                raise ValueError(
                    f"LLM extraction {field_path} cites unknown evidence: {unknown}"
                )
            if required and not normalized:
                raise ValueError(f"LLM extraction {field_path} requires evidence")
            for evidence_id in normalized:
                supports[evidence_id].add(field_path)
            return normalized

        expected_indicators = {
            "technical_autonomy",
            "innovation_capability",
            "intellectual_property",
            "technical_maturity",
        }
        unknown_indicators = set(result.technology_indicators) - expected_indicators
        if unknown_indicators:
            raise ValueError(
                f"LLM extraction contains unknown indicators: {sorted(unknown_indicators)}"
            )

        summary_ids = checked_ids(
            result.summary_evidence_ids,
            "technology_summary",
            required=result.summary_status == "supported",
        )
        if result.summary_status == "insufficient_evidence":
            refusal = "证据不足：当前提供的检索片段无法支持技术事实摘要。"
            if result.technology_summary.strip() != refusal or summary_ids:
                raise ValueError(
                    "LLM extraction insufficient_evidence summary must use the exact refusal text and no evidence IDs"
                )
        citation_pattern = re.compile(r"\[([A-Z]\d+)\]")
        text_fields = [result.technology_summary]
        text_fields.extend(item.content for item in result.strengths)
        text_fields.extend(item.content for item in result.risks)
        unknown_text_ids = sorted(
            {
                evidence_id
                for text in text_fields
                for evidence_id in citation_pattern.findall(text)
                if evidence_id not in valid_ids
            }
        )
        if unknown_text_ids:
            raise ValueError(
                "LLM extraction text cites unknown evidence: "
                f"{unknown_text_ids}"
            )
        summary_text_ids = set(citation_pattern.findall(result.technology_summary))
        if not summary_text_ids <= set(summary_ids):
            raise ValueError("LLM extraction technology_summary cites evidence not declared in summary_evidence_ids")
        summary = result.technology_summary + "".join(
            f"[{evidence_id}]" for evidence_id in summary_ids
        )
        raw_indicators = {}
        for indicator_id in expected_indicators:
            extraction = result.technology_indicators.get(indicator_id)
            if extraction is None:
                raw_indicators[indicator_id] = TechnologyIndicatorAssessment(
                    rationale="LLM 未返回该指标，证据不足，指标未评分。"
                )
                continue
            evidence = checked_ids(
                extraction.evidence_ids,
                f"technology_indicators.{indicator_id}",
                required=extraction.score is not None,
            )
            rationale_ids = set(citation_pattern.findall(extraction.rationale))
            if not rationale_ids <= set(evidence):
                raise ValueError(f"LLM extraction {indicator_id} rationale cites evidence not bound to the indicator")
            raw_indicators[indicator_id] = TechnologyIndicatorAssessment(
                score=extraction.score,
                evidence=evidence,
                rationale=extraction.rationale,
            )

        strengths: list[str] = []
        for index, finding in enumerate(result.strengths):
            evidence = checked_ids(
                finding.evidence_ids, f"strengths[{index}]", required=True
            )
            if not set(citation_pattern.findall(finding.content)) <= set(evidence):
                raise ValueError(f"LLM extraction strengths[{index}] text cites unbound evidence")
            strengths.append(f"{finding.content}" + "".join(f"[{item}]" for item in evidence))
        risks: list[str] = []
        for index, finding in enumerate(result.risks):
            evidence = checked_ids(
                finding.evidence_ids, f"risks[{index}]", required=True
            )
            if not set(citation_pattern.findall(finding.content)) <= set(evidence):
                raise ValueError(f"LLM extraction risks[{index}] text cites unbound evidence")
            risks.append(f"{finding.content}" + "".join(f"[{item}]" for item in evidence))

        evidence = []
        for item in evidence_items:
            evidence_id = evidence_ids[item.chunk_id]
            if not supports[evidence_id]:
                continue
            evidence.append(
                TechnologyEvidence(
                    evidence_id=evidence_id,
                    document_name=item.document_name,
                    page_number=item.page_number,
                    chunk_id=item.chunk_id,
                    excerpt=item.text,
                    retrieval_score=round(item.score, 4),
                    supports=sorted(supports[evidence_id]),
                )
            )

        return TechnologyAnalysis(
            technology_indicators=TechnologyIndicators(**raw_indicators),
            technology_summary=summary,
            summary_status=result.summary_status,
            strengths=strengths,
            risks=risks,
            evidence=evidence,
        )

    @staticmethod
    def _load_prompt() -> str:
        prompt_path = Path(__file__).resolve().parents[3] / "prompts" / "technology_agent_prompt.md"
        if prompt_path.is_file():
            return prompt_path.read_text(encoding="utf-8")
        return "Extract only evidence-bound technology indicators; never calculate a final score."

    async def _retrieve(
        self, request: TechnologyAgentRequest
    ) -> list[RetrievedEvidence]:
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
    def _empty_indicators() -> TechnologyIndicators:
        missing = "证据不足，指标未评分。"
        return TechnologyIndicators(
            technical_autonomy=TechnologyIndicatorAssessment(rationale=missing),
            innovation_capability=TechnologyIndicatorAssessment(rationale=missing),
            intellectual_property=TechnologyIndicatorAssessment(rationale=missing),
            technical_maturity=TechnologyIndicatorAssessment(rationale=missing),
        )

    def _technical_autonomy(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> TechnologyIndicatorAssessment:
        item = self._first_match(evidence, ("自主研发", "自研"))
        if item:
            evidence_id = reference(item, "technology_indicators.technical_autonomy")
            return TechnologyIndicatorAssessment(
                score=80,
                evidence=[evidence_id],
                rationale="材料明确描述自主研发的核心算法或技术能力；尚需源代码及权属核验。",
            )
        item = self._first_match(evidence, ("核心技术", "核心算法"))
        if item:
            evidence_id = reference(item, "technology_indicators.technical_autonomy")
            return TechnologyIndicatorAssessment(
                score=60,
                evidence=[evidence_id],
                rationale="材料描述核心技术，但自主研发和外部依赖程度证据有限。",
            )
        item = self._first_match(evidence, ("依赖第三方", "外部依赖"))
        if item:
            evidence_id = reference(item, "technology_indicators.technical_autonomy")
            return TechnologyIndicatorAssessment(
                score=35,
                evidence=[evidence_id],
                rationale="材料仅能确认明显外部依赖，未检索到自主核心能力证据。",
            )
        return TechnologyIndicatorAssessment(rationale="未检索到技术自主性有效证据。")

    def _innovation_capability(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> TechnologyIndicatorAssessment:
        groups = (
            ("技术方案", "算法", "模型压缩", "小样本"),
            ("研发团队", "研发人员", "研发投入"),
            ("数据标注", "模型训练", "边缘推理", "结果追溯"),
        )
        matched_items: list[RetrievedEvidence] = []
        group_hits = 0
        for group in groups:
            item = self._first_match(evidence, group)
            if item:
                group_hits += 1
                if item not in matched_items:
                    matched_items.append(item)
        if not matched_items:
            return TechnologyIndicatorAssessment(rationale="未检索到可核验的创新活动证据。")
        score = min(100, 40 + group_hits * 15)
        evidence_ids = [
            reference(item, "technology_indicators.innovation_capability")
            for item in matched_items
        ]
        return TechnologyIndicatorAssessment(
            score=score,
            evidence=evidence_ids,
            rationale=(
                f"材料命中 {group_hits} 类创新活动证据；该分值是指标量表输入，"
                "不包含最终权重计算。"
            ),
        )

    def _intellectual_property(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> TechnologyIndicatorAssessment:
        granted = self._first_affirmed_match(evidence, ("获得授权", "授权专利"))
        if granted:
            evidence_id = reference(
                granted, "technology_indicators.intellectual_property"
            )
            return TechnologyIndicatorAssessment(
                score=80,
                evidence=[evidence_id],
                rationale="材料包含已授权知识产权记录，仍需核验权属及与核心技术的关联。",
            )
        pending = self._first_match(evidence, ("实质审查", "未获得授权"))
        if pending:
            evidence_id = reference(
                pending, "technology_indicators.intellectual_property"
            )
            return TechnologyIndicatorAssessment(
                score=55,
                evidence=[evidence_id],
                rationale="已开展专利布局，但相关申请尚未授权，权利稳定性待核验。",
            )
        application = self._first_match(evidence, ("专利申请", "软件著作权"))
        if application:
            evidence_id = reference(
                application, "technology_indicators.intellectual_property"
            )
            return TechnologyIndicatorAssessment(
                score=40,
                evidence=[evidence_id],
                rationale="材料仅能确认知识产权申请或登记活动，质量与权利状态证据有限。",
            )
        return TechnologyIndicatorAssessment(rationale="未检索到知识产权有效证据。")

    def _technical_maturity(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> TechnologyIndicatorAssessment:
        production = self._first_affirmed_match(
            evidence,
            ("规模化部署", "批量交付", "量产", "正式商业运营"),
            required_markers=("已进入", "已完成", "已实现", "正在", "实现量产"),
        )
        if production:
            evidence_id = reference(
                production, "technology_indicators.technical_maturity"
            )
            return TechnologyIndicatorAssessment(
                score=85,
                evidence=[evidence_id],
                rationale="TRL 8：材料显示系统已进入规模化部署或批量交付。",
            )
        pilot = self._first_match(
            evidence,
            ("开展试点", "中试验证", "客户现场验证", "在生产线上", "连续运行"),
        )
        if pilot:
            evidence_id = reference(
                pilot, "technology_indicators.technical_maturity"
            )
            return TechnologyIndicatorAssessment(
                score=65,
                evidence=[evidence_id],
                rationale="TRL 6：原型已在相关环境完成有限试点，尚未证明规模化能力。",
            )
        prototype = self._first_match(evidence, ("原型", "样机", "实验室验证"))
        if prototype:
            evidence_id = reference(
                prototype, "technology_indicators.technical_maturity"
            )
            return TechnologyIndicatorAssessment(
                score=45,
                evidence=[evidence_id],
                rationale="TRL 4：材料显示已完成实验室原型或样机验证。",
            )
        concept = self._first_match(evidence, ("技术方案", "算法方案", "研发方向"))
        if concept:
            evidence_id = reference(
                concept, "technology_indicators.technical_maturity"
            )
            return TechnologyIndicatorAssessment(
                score=25,
                evidence=[evidence_id],
                rationale="TRL 2：资料仅能支持早期技术概念判断。",
            )
        return TechnologyIndicatorAssessment(rationale="未检索到技术成熟度有效证据。")

    @staticmethod
    def _first_match(
        evidence: list[RetrievedEvidence], keywords: tuple[str, ...]
    ) -> RetrievedEvidence | None:
        return next(
            (item for item in evidence if any(word in item.text for word in keywords)),
            None,
        )

    @staticmethod
    def _first_affirmed_match(
        evidence: list[RetrievedEvidence],
        keywords: tuple[str, ...],
        required_markers: tuple[str, ...] = (),
    ) -> RetrievedEvidence | None:
        non_factual_markers = (
            "尚未",
            "未完成",
            "未获得",
            "待验证",
            "建议",
            "计划",
            "预计",
            "拟",
            "目标",
            "需要",
            "需提供",
            "不能",
        )
        for item in evidence:
            clauses = re.split(r"[。！？；]", item.text)
            for clause in clauses:
                if not any(keyword in clause for keyword in keywords):
                    continue
                if any(marker in clause for marker in non_factual_markers):
                    continue
                if required_markers and not any(
                    marker in clause for marker in required_markers
                ):
                    continue
                return item
        return None

    def _strengths(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> list[str]:
        definitions = (
            (("自主研发", "自研", "核心技术"), "材料显示已形成自主研发或核心技术积累"),
            (("专利", "知识产权", "软著"), "材料显示已开展知识产权布局"),
            (
                ("试点", "客户现场", "生产线", "连续运行"),
                "材料显示技术原型已进入真实或近真实环境验证",
            ),
        )
        strengths: list[str] = []
        for keywords, conclusion in definitions:
            item = self._first_match(evidence, keywords)
            if item:
                field_path = f"strengths[{len(strengths)}]"
                strengths.append(f"{conclusion}[{reference(item, field_path)}]")
        return strengths

    def _risks(
        self, evidence: list[RetrievedEvidence], reference: EvidenceReference
    ) -> list[str]:
        definitions = (
            (
                ("范围有限", "仅覆盖", "规模化效果待验证"),
                "现有验证范围有限，规模化效果需要更多独立样本确认",
            ),
            (
                ("依赖第三方", "外部依赖"),
                "技术效果或交付可能依赖第三方组件及外部环境",
            ),
            (
                ("实质审查", "未授权", "未获得授权", "专利申请"),
                "知识产权仍处于申请或审查阶段，权利稳定性需要持续核验",
            ),
        )
        risks: list[str] = []
        for keywords, conclusion in definitions:
            item = self._first_match(evidence, keywords)
            if item:
                field_path = f"risks[{len(risks)}]"
                risks.append(f"{conclusion}[{reference(item, field_path)}]")
        return risks


def _compress_llm_evidence(evidence_items: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    """Compatibility wrapper for previous internal imports."""
    return assemble_context(evidence_items)[0]
