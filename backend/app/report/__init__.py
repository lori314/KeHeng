"""Structured technology report contracts and deterministic generator."""

from app.report.generator import (
    EvaluationResultInput,
    ReportEvidence,
    ReportFinding,
    ReportGenerator,
    ReportRequest,
    TechnologyReport,
)
from app.report.comprehensive import (
    ComprehensiveReport,
    ComprehensiveReportGenerator,
    ComprehensiveReportRequest,
    IndustryReportSection,
)

__all__ = [
    "EvaluationResultInput",
    "ReportEvidence",
    "ReportFinding",
    "ReportGenerator",
    "ReportRequest",
    "TechnologyReport",
    "ComprehensiveReport",
    "ComprehensiveReportGenerator",
    "ComprehensiveReportRequest",
    "IndustryReportSection",
]
