"""Deterministic report assembly for technology plus industry evaluation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.agents.industry_agent import IndustryAnalysis, IndustryIndicators
from app.agents.technology_agent import TechnologyAnalysis
from app.report.generator import (
    EvaluationResultInput,
    ReportEvidence,
    ReportFinding,
    ReportGenerator,
    ReportRequest,
)


class IndustryReportSection(BaseModel):
    score: float | None
    indicators: IndustryIndicators
    strengths: list[ReportFinding]
    risks: list[ReportFinding]
    evaluation_details: dict[str, Any]


class ComprehensiveReport(BaseModel):
    """User-facing report containing the original technology and new industry section."""

    model_config = ConfigDict(extra="forbid")

    task_id: str | None
    enterprise_name: str
    title: str
    summary: str
    technology_score: float | None
    industry_score: float | None
    overall_score: float | None
    assessment_status: str
    evidence_coverage: dict[str, float]
    dimension_scores: dict[str, float | None]
    strengths: list[ReportFinding]
    risks: list[ReportFinding]
    industry_analysis: IndustryReportSection
    evaluation_details: dict[str, Any]
    references: list[ReportEvidence]


class ComprehensiveReportRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str | None = None
    enterprise_name: str = Field(default="未命名企业", min_length=1, max_length=120)
    technology_analysis: TechnologyAnalysis
    industry_analysis: IndustryAnalysis
    technology_evaluation: EvaluationResultInput
    industry_evaluation: Mapping[str, Any]
    comprehensive_evaluation: Mapping[str, Any]


class ComprehensiveReportGenerator:
    """Extend the existing deterministic generator without changing its contract."""

    title = "科衡科技企业技术与产业价值分析报告"
    _citation_pattern = re.compile(r"\[([A-Z]\d+)\]")

    def __init__(self) -> None:
        self._technology_generator = ReportGenerator()

    def generate(self, request: ComprehensiveReportRequest) -> ComprehensiveReport:
        technology_report = self._technology_generator.generate(
            ReportRequest(
                task_id=request.task_id,
                enterprise_name=request.enterprise_name,
                technology_analysis=request.technology_analysis,
                evaluation_result=request.technology_evaluation,
            )
        )
        references = list(technology_report.references)
        evidence_index = {item.evidence_id: item for item in references}
        for item in request.industry_analysis.evidence:
            reference = ReportEvidence(
                evidence_id=item.evidence_id,
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                excerpt=item.excerpt,
                retrieval_score=item.retrieval_score,
            )
            existing = evidence_index.get(reference.evidence_id)
            if existing is not None and existing != reference:
                raise ValueError(
                    f"Industry analysis evidence ID conflicts with technology evidence: "
                    f"{reference.evidence_id}"
                )
            if existing is None:
                evidence_index[reference.evidence_id] = reference
                references.append(reference)

        self._validate_industry_evidence(request.industry_analysis, evidence_index)
        industry_evaluation = dict(request.industry_evaluation)
        self._validate_evaluation_mapping(industry_evaluation, evidence_index, "产业")
        industry_score = _number_or_none(industry_evaluation.get("industry_score"))
        industry_findings = self._industry_findings(
            request.industry_analysis, evidence_index
        )
        comprehensive = dict(request.comprehensive_evaluation)
        return ComprehensiveReport(
            task_id=request.task_id,
            enterprise_name=request.enterprise_name,
            title=self.title,
            summary=(
                f"{technology_report.summary} 产业指标仅基于资料中可定位的行业、市场、竞争和政策事实；"
                "产业趋势不等同于企业成功。"
            ),
            technology_score=technology_report.technology_score,
            industry_score=industry_score,
            overall_score=_number_or_none(comprehensive.get("overall_score")),
            assessment_status=str(comprehensive.get("assessment_status", "pipeline_failed")),
            evidence_coverage=dict(comprehensive.get("evidence_coverage") or {}),
            dimension_scores=dict(comprehensive.get("dimension_scores") or {}),
            strengths=technology_report.strengths,
            risks=technology_report.risks,
            industry_analysis=IndustryReportSection(
                score=industry_score,
                indicators=request.industry_analysis.industry_indicators,
                strengths=industry_findings[0],
                risks=industry_findings[1],
                evaluation_details=industry_evaluation,
            ),
            evaluation_details={
                "technology": request.technology_evaluation.model_dump(mode="json"),
                "industry": industry_evaluation,
                "comprehensive": comprehensive,
            },
            references=references,
        )

    def _validate_industry_evidence(
        self,
        analysis: IndustryAnalysis,
        evidence_index: Mapping[str, ReportEvidence],
    ) -> None:
        for indicator_id, indicator in analysis.industry_indicators:
            if indicator.score is None:
                continue
            if not indicator.evidence:
                raise ValueError(f"已评分产业指标 {indicator_id} 缺少证据")
            missing = [item for item in indicator.evidence if item not in evidence_index]
            if missing:
                raise ValueError(
                    f"产业指标 {indicator_id} 引用了不存在的证据: {', '.join(missing)}"
                )
        for mapping in self._industry_evidence_mapping(analysis):
            missing = [item for item in mapping if item not in evidence_index]
            if missing:
                raise ValueError(f"产业评价引用了不存在的证据: {', '.join(missing)}")

    @staticmethod
    def _validate_evaluation_mapping(
        evaluation: Mapping[str, Any],
        evidence_index: Mapping[str, ReportEvidence],
        label: str,
    ) -> None:
        for mapping in evaluation.get("evidence_mapping") or []:
            missing = [
                str(item)
                for item in mapping.get("evidence_ids", [])
                if str(item) not in evidence_index
            ]
            if missing:
                raise ValueError(f"{label}评价引用了不存在的证据: {', '.join(missing)}")

    @staticmethod
    def _industry_evidence_mapping(analysis: IndustryAnalysis) -> list[list[str]]:
        return [
            [str(item) for item in indicator.evidence]
            for _, indicator in analysis.industry_indicators
            if indicator.evidence
        ]

    def _industry_findings(
        self,
        analysis: IndustryAnalysis,
        evidence_index: Mapping[str, ReportEvidence],
    ) -> tuple[list[ReportFinding], list[ReportFinding]]:
        strengths: list[ReportFinding] = []
        risks: list[ReportFinding] = []
        for indicator_id, indicator in analysis.industry_indicators:
            if indicator.score is None or not indicator.evidence:
                continue
            label = {
                "market_potential": "市场潜力",
                "industry_growth": "行业成长性",
                "competitive_position": "竞争位置",
                "policy_environment": "政策环境",
            }.get(indicator_id, indicator_id)
            citations = "".join(f"[{item}]" for item in indicator.evidence)
            finding = ReportFinding(
                content=f"{label}：{indicator.rationale}{citations}",
                evidence=[evidence_index[item] for item in indicator.evidence],
            )
            if indicator.score >= 60:
                strengths.append(finding)
            elif indicator.score <= 40:
                risks.append(finding)
        return strengths, risks


def _number_or_none(value: Any) -> float | None:
    return None if value is None else float(value)
