"""Deterministic structured technology report assembly.

This module copies validated Agent and Evaluation outputs into a user-facing
report. It never recalculates a score or introduces new analytical findings.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.technology_agent import TechnologyAnalysis, TechnologyIndicators


class EvaluationResultInput(BaseModel):
    """Serializable contract produced by the root Evaluation Engine."""

    model_config = ConfigDict(extra="forbid")

    technology_score: float | None = Field(default=None, ge=0, le=100)
    dimension_scores: dict[str, float | None]
    score_explanation: list[dict[str, Any]] = Field(default_factory=list)
    evidence_mapping: list[dict[str, Any]] = Field(default_factory=list)


class ReportRequest(BaseModel):
    task_id: str | None = None
    enterprise_name: str = Field(default="未命名企业", min_length=1, max_length=120)
    technology_analysis: TechnologyAnalysis
    evaluation_result: EvaluationResultInput


class ReportEvidence(BaseModel):
    evidence_id: str
    document_name: str
    page_number: int | None
    chunk_id: str
    excerpt: str
    retrieval_score: float


class ReportFinding(BaseModel):
    content: str
    evidence: list[ReportEvidence]


class ReportEvaluationDetails(BaseModel):
    indicators: TechnologyIndicators
    score_explanation: list[dict[str, Any]]
    evidence_mapping: list[dict[str, Any]]


class TechnologyReport(BaseModel):
    """Stable JSON report returned by the API and consumed by the Web page."""

    model_config = ConfigDict(extra="forbid")

    task_id: str | None
    enterprise_name: str
    title: str
    summary: str
    technology_score: float | None
    dimension_scores: dict[str, float | None]
    strengths: list[ReportFinding]
    risks: list[ReportFinding]
    evaluation_details: ReportEvaluationDetails
    references: list[ReportEvidence]


class ReportGenerator:
    """Assemble a report while validating that every cited E ID exists."""

    title = "科衡科技企业技术价值分析报告"
    _citation_pattern = re.compile(r"\[([A-Z]\d+)\]")

    def generate(self, request: ReportRequest) -> TechnologyReport:
        analysis = request.technology_analysis
        evaluation = request.evaluation_result
        references = [
            ReportEvidence(
                evidence_id=item.evidence_id,
                document_name=item.document_name,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                excerpt=item.excerpt,
                retrieval_score=item.retrieval_score,
            )
            for item in analysis.evidence
        ]
        evidence_index = {item.evidence_id: item for item in references}
        if len(evidence_index) != len(references):
            raise ValueError("Technology analysis contains duplicate evidence IDs")

        self._validate_summary(analysis.technology_summary, evidence_index)
        self._validate_indicator_evidence(analysis, evidence_index)
        self._validate_evaluation_evidence(evaluation, evidence_index)

        return TechnologyReport(
            task_id=request.task_id,
            enterprise_name=request.enterprise_name,
            title=self.title,
            summary=analysis.technology_summary,
            technology_score=evaluation.technology_score,
            dimension_scores=dict(evaluation.dimension_scores),
            strengths=self._findings(analysis.strengths, evidence_index, "优势"),
            risks=self._findings(analysis.risks, evidence_index, "风险"),
            evaluation_details=ReportEvaluationDetails(
                indicators=analysis.technology_indicators,
                score_explanation=list(evaluation.score_explanation),
                evidence_mapping=list(evaluation.evidence_mapping),
            ),
            references=references,
        )

    def generate_comprehensive(self, request: Any) -> Any:
        """Generate the opt-in v0.7 report without changing the v0.4 contract."""

        from app.report.comprehensive import ComprehensiveReportGenerator

        return ComprehensiveReportGenerator().generate(request)

    def _findings(
        self,
        contents: list[str],
        evidence_index: dict[str, ReportEvidence],
        finding_type: str,
    ) -> list[ReportFinding]:
        findings: list[ReportFinding] = []
        for content in contents:
            evidence_ids = list(dict.fromkeys(self._citation_pattern.findall(content)))
            if not evidence_ids:
                raise ValueError(f"{finding_type}结论缺少 E 编号证据引用: {content}")
            missing = [item for item in evidence_ids if item not in evidence_index]
            if missing:
                raise ValueError(
                    f"{finding_type}结论引用了不存在的证据: {', '.join(missing)}"
                )
            findings.append(
                ReportFinding(
                    content=content,
                    evidence=[evidence_index[item] for item in evidence_ids],
                )
            )
        return findings

    def _validate_summary(
        self, summary: str, evidence_index: dict[str, ReportEvidence]
    ) -> None:
        evidence_ids = list(dict.fromkeys(self._citation_pattern.findall(summary)))
        if evidence_index and not evidence_ids:
            raise ValueError("技术摘要缺少 E 编号证据引用")
        missing = [item for item in evidence_ids if item not in evidence_index]
        if missing:
            raise ValueError(f"技术摘要引用了不存在的证据: {', '.join(missing)}")

    @staticmethod
    def _validate_indicator_evidence(
        analysis: TechnologyAnalysis,
        evidence_index: dict[str, ReportEvidence],
    ) -> None:
        for indicator_id, indicator in analysis.technology_indicators:
            if indicator.score is None:
                continue
            if not indicator.evidence:
                raise ValueError(f"已评分指标 {indicator_id} 缺少证据")
            missing = [item for item in indicator.evidence if item not in evidence_index]
            if missing:
                raise ValueError(
                    f"指标 {indicator_id} 引用了不存在的证据: {', '.join(missing)}"
                )

    @staticmethod
    def _validate_evaluation_evidence(
        evaluation: EvaluationResultInput,
        evidence_index: dict[str, ReportEvidence],
    ) -> None:
        for mapping in evaluation.evidence_mapping:
            indicator_id = str(mapping.get("indicator_id", "unknown"))
            evidence_ids = [str(item) for item in mapping.get("evidence_ids", [])]
            missing = [item for item in evidence_ids if item not in evidence_index]
            if missing:
                raise ValueError(
                    f"评价指标 {indicator_id} 引用了不存在的证据: "
                    f"{', '.join(missing)}"
                )
