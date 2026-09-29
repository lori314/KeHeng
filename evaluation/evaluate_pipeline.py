"""Run quality checks against the v0.5.5 cases in rule or LLM mode."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisArtifacts,
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from app.llm.provider import LLMProvider  # noqa: E402
from app.llm.mock import MockLLMProvider  # noqa: E402


DEFAULT_CASE_ROOT = ROOT / "data" / "evaluation_cases"
INDICATOR_IDS = (
    "technical_autonomy",
    "innovation_capability",
    "intellectual_property",
    "technical_maturity",
)
DIMENSION_IDS = ("innovation", "ip", "maturity")
EVIDENCE_PATTERN = re.compile(r"\[([A-Z]\d+)\]")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    directory: Path
    pdf_path: Path
    expected_indicators: dict[str, Any]
    expected_evidence: dict[str, Any]

    @property
    def task_id(self) -> str:
        return f"quality-{self.case_id.replace('_', '-')}"

    @property
    def enterprise_name(self) -> str:
        return str(self.expected_indicators["enterprise_name"])


def load_cases(case_root: str | Path = DEFAULT_CASE_ROOT) -> list[EvaluationCase]:
    """Load and validate the on-disk evaluation case contract."""

    root = Path(case_root).resolve()
    cases: list[EvaluationCase] = []
    for directory in sorted(path for path in root.glob("case_*") if path.is_dir()):
        required = {
            "pdf": directory / "company_profile.pdf",
            "indicators": directory / "expected_indicators.json",
            "evidence": directory / "expected_evidence.json",
            "readme": directory / "README.md",
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"{directory.name} is missing required files: {', '.join(missing)}"
            )
        expected_indicators = _load_json(required["indicators"])
        expected_evidence = _load_json(required["evidence"])
        if expected_indicators.get("case_id") != directory.name:
            raise ValueError(f"Indicator case_id mismatch in {directory}")
        if expected_evidence.get("case_id") != directory.name:
            raise ValueError(f"Evidence case_id mismatch in {directory}")
        cases.append(
            EvaluationCase(
                case_id=directory.name,
                directory=directory,
                pdf_path=required["pdf"],
                expected_indicators=expected_indicators,
                expected_evidence=expected_evidence,
            )
        )
    if not cases:
        raise ValueError(f"No evaluation cases found under {root}")
    return cases


async def evaluate_cases(
    case_root: str | Path = DEFAULT_CASE_ROOT,
    runtime_root: str | Path | None = None,
    agent_mode: str = "rule",
    llm_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Run all cases once and return aggregate rates plus case diagnostics."""

    cases = load_cases(case_root)
    resolved_runtime = Path(
        runtime_root or ROOT / "runtime" / "pipeline_evaluation"
    ).resolve()
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=resolved_runtime,
        agent_mode=agent_mode,
        llm_provider=llm_provider,
    )
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            artifacts = await service.run_with_artifacts(
                TechnologyAnalysisInput(
                    task_id=case.task_id,
                    enterprise_name=case.enterprise_name,
                    pdf_path=case.pdf_path,
                    original_file_name=case.pdf_path.name,
                )
            )
            results.append(_evaluate_success(case, artifacts))
        except Exception as exc:
            results.append(_evaluate_failure(case, exc))
    aggregate = _aggregate(results)
    aggregate["agent_mode"] = agent_mode
    return aggregate


def _evaluate_success(
    case: EvaluationCase, artifacts: TechnologyAnalysisArtifacts
) -> dict[str, Any]:
    analysis = artifacts.technology_analysis.model_dump(mode="json")
    evaluation = artifacts.evaluation_result.model_dump(mode="json")
    report = artifacts.report.model_dump(mode="json")

    indicator_passed, indicator_total, indicator_scores = _indicator_matches(
        analysis, case.expected_indicators
    )
    score_matches, score_errors = _score_matches(
        evaluation, case.expected_indicators
    )
    evidence_passed, evidence_total, missing_evidence = _expected_evidence_hits(
        report, case.expected_evidence
    )
    citation_passed, citation_total = _citation_completeness(
        analysis, evaluation, report
    )
    field_passed, field_total, missing_fields = _critical_field_completeness(
        report
    )
    isolated, isolation_errors = _task_isolation(case, report)
    constraint_errors = _constraint_errors(analysis, case.expected_indicators)

    errors = [
        *score_errors,
        *[f"missing expected evidence: {item}" for item in missing_evidence],
        *[f"missing critical field: {item}" for item in missing_fields],
        *isolation_errors,
        *constraint_errors,
    ]
    passed = all(
        (
            indicator_passed == indicator_total,
            score_matches,
            evidence_passed == evidence_total,
            citation_passed == citation_total,
            field_passed == field_total,
            isolated,
            not constraint_errors,
        )
    )
    return {
        "case_id": case.case_id,
        "task_id": case.task_id,
        "report_generated": True,
        "passed": passed,
        "technology_score": evaluation.get("technology_score"),
        "dimension_scores": evaluation.get("dimension_scores"),
        "indicator_scores": indicator_scores,
        "indicator_expectations": {
            "passed": indicator_passed,
            "total": indicator_total,
        },
        "expected_evidence": {
            "passed": evidence_passed,
            "total": evidence_total,
        },
        "citations": {"passed": citation_passed, "total": citation_total},
        "critical_fields": {"passed": field_passed, "total": field_total},
        "task_isolated": isolated,
        "errors": errors,
    }


def _evaluate_failure(case: EvaluationCase, exc: Exception) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "task_id": case.task_id,
        "report_generated": False,
        "passed": False,
        "technology_score": None,
        "dimension_scores": {},
        "indicator_scores": {},
        "indicator_expectations": {"passed": 0, "total": len(INDICATOR_IDS)},
        "expected_evidence": {
            "passed": 0,
            "total": len(case.expected_evidence.get("required_evidence", [])),
        },
        "citations": {"passed": 0, "total": 1},
        "critical_fields": {"passed": 0, "total": 11},
        "task_isolated": False,
        "errors": [f"{type(exc).__name__}: {exc}"],
    }


def _indicator_matches(
    analysis: Mapping[str, Any], expected: Mapping[str, Any]
) -> tuple[int, int, dict[str, float | None]]:
    actual_indicators = analysis.get("technology_indicators") or {}
    expected_indicators = expected["expected_indicators"]
    tolerance = float(expected.get("score_tolerance", 0.001))
    passed = 0
    scores: dict[str, float | None] = {}
    for indicator_id in INDICATOR_IDS:
        actual = actual_indicators.get(indicator_id) or {}
        expected_item = expected_indicators[indicator_id]
        actual_score = actual.get("score")
        scores[indicator_id] = actual_score
        score_match = _nullable_number_matches(
            actual_score, expected_item.get("score"), tolerance
        )
        evidence_match = bool(actual.get("evidence")) == bool(
            expected_item.get("evidence_required")
        )
        passed += int(score_match and evidence_match)
    return passed, len(INDICATOR_IDS), scores


def _score_matches(
    evaluation: Mapping[str, Any], expected: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    tolerance = float(expected.get("score_tolerance", 0.001))
    errors: list[str] = []
    if not _nullable_number_matches(
        evaluation.get("technology_score"),
        expected.get("expected_technology_score"),
        tolerance,
    ):
        errors.append(
            "technology score mismatch: "
            f"actual={evaluation.get('technology_score')}, "
            f"expected={expected.get('expected_technology_score')}"
        )
    actual_dimensions = evaluation.get("dimension_scores") or {}
    for dimension_id in DIMENSION_IDS:
        actual = actual_dimensions.get(dimension_id)
        expected_value = expected["expected_dimension_scores"].get(dimension_id)
        if not _nullable_number_matches(actual, expected_value, tolerance):
            errors.append(
                f"dimension {dimension_id} mismatch: "
                f"actual={actual}, expected={expected_value}"
            )
    return not errors, errors


def _expected_evidence_hits(
    report: Mapping[str, Any], expected: Mapping[str, Any]
) -> tuple[int, int, list[str]]:
    references = report.get("references") or []
    required = expected.get("required_evidence") or []
    passed = 0
    missing: list[str] = []
    for requirement in required:
        found = any(
            reference.get("page_number") == requirement.get("page_number")
            and all(
                _compact_text(phrase) in _compact_text(reference.get("excerpt", ""))
                for phrase in requirement.get("contains_all", [])
            )
            for reference in references
        )
        if found:
            passed += 1
        else:
            missing.append(str(requirement.get("evidence_key", "unknown")))
    return passed, len(required), missing


def _citation_completeness(
    analysis: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[int, int]:
    references = {
        str(item.get("evidence_id")): item
        for item in report.get("references") or []
        if item.get("evidence_id")
    }
    citation_ids = EVIDENCE_PATTERN.findall(str(analysis.get("technology_summary", "")))
    for field in ("strengths", "risks"):
        for content in analysis.get(field) or []:
            citation_ids.extend(EVIDENCE_PATTERN.findall(str(content)))
    for indicator in (analysis.get("technology_indicators") or {}).values():
        citation_ids.extend(str(item) for item in indicator.get("evidence") or [])
    for mapping in evaluation.get("evidence_mapping") or []:
        citation_ids.extend(str(item) for item in mapping.get("evidence_ids") or [])

    passed = 0
    for evidence_id in citation_ids:
        reference = references.get(evidence_id)
        if reference and all(
            (
                re.fullmatch(r"E\d+", evidence_id),
                bool(reference.get("document_name")),
                isinstance(reference.get("page_number"), int),
                bool(reference.get("chunk_id")),
                bool(reference.get("excerpt")),
            )
        ):
            passed += 1
    return passed, len(citation_ids)


def _critical_field_completeness(
    report: Mapping[str, Any],
) -> tuple[int, int, list[str]]:
    checks = {
        "task_id": bool(report.get("task_id")),
        "enterprise_name": bool(report.get("enterprise_name")),
        "title": bool(report.get("title")),
        "summary": bool(report.get("summary")),
        "technology_score": "technology_score" in report,
        "dimension_scores": all(
            item in (report.get("dimension_scores") or {}) for item in DIMENSION_IDS
        ),
        "strengths": isinstance(report.get("strengths"), list),
        "risks": isinstance(report.get("risks"), list),
        "evaluation_details.indicators": all(
            item
            in ((report.get("evaluation_details") or {}).get("indicators") or {})
            for item in INDICATOR_IDS
        ),
        "evaluation_details.score_explanation": isinstance(
            (report.get("evaluation_details") or {}).get("score_explanation"), list
        ),
        "references": isinstance(report.get("references"), list),
    }
    missing = [name for name, valid in checks.items() if not valid]
    return sum(checks.values()), len(checks), missing


def _task_isolation(
    case: EvaluationCase, report: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    expected = case.expected_evidence
    errors: list[str] = []
    if report.get("task_id") != case.task_id:
        errors.append("report task_id does not match the case task")
    references = report.get("references") or []
    source_document = expected["source_document"]
    if any(item.get("document_name") != source_document for item in references):
        errors.append("report contains a document from another task")
    excerpts = "\n".join(str(item.get("excerpt", "")) for item in references)
    if expected["case_marker"] not in excerpts:
        errors.append("current case marker is absent from retrieved evidence")
    for marker in expected.get("forbidden_case_markers", []):
        if marker in excerpts:
            errors.append(f"foreign case marker found: {marker}")
    return not errors, errors


def _constraint_errors(
    analysis: Mapping[str, Any], expected: Mapping[str, Any]
) -> list[str]:
    indicators = analysis.get("technology_indicators") or {}
    constraints = expected.get("constraints") or {}
    errors: list[str] = []
    scored_count = sum(
        item.get("score") is not None for item in indicators.values()
    )
    expected_count = constraints.get("expected_scored_indicator_count")
    if expected_count is not None and scored_count != expected_count:
        errors.append(
            f"scored indicator count mismatch: actual={scored_count}, "
            f"expected={expected_count}"
        )
    maturity = indicators.get("technical_maturity") or {}
    maximum = constraints.get("technical_maturity_max")
    maturity_score = maturity.get("score")
    if maximum is not None and maturity_score is not None and maturity_score > maximum:
        errors.append(
            f"technical maturity over-inferred: {maturity_score} > {maximum}"
        )
    rationale = str(maturity.get("rationale", ""))
    for term in constraints.get("forbidden_rationale_terms", []):
        if term in rationale:
            errors.append(f"forbidden maturity rationale term found: {term}")
    return errors


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(results)
    report_passed = sum(item["report_generated"] for item in results)
    isolation_passed = sum(item["task_isolated"] for item in results)
    citation_passed = sum(item["citations"]["passed"] for item in results)
    citation_total = sum(item["citations"]["total"] for item in results)
    field_passed = sum(item["critical_fields"]["passed"] for item in results)
    field_total = sum(item["critical_fields"]["total"] for item in results)
    indicator_passed = sum(
        item["indicator_expectations"]["passed"] for item in results
    )
    indicator_total = sum(
        item["indicator_expectations"]["total"] for item in results
    )
    evidence_passed = sum(item["expected_evidence"]["passed"] for item in results)
    evidence_total = sum(item["expected_evidence"]["total"] for item in results)
    return {
        "schema_version": "1.0",
        "case_count": case_count,
        "passed_case_count": sum(item["passed"] for item in results),
        "metrics": {
            "report_generation_success_rate": _percentage(report_passed, case_count),
            "evidence_reference_completeness_rate": _percentage(
                citation_passed, citation_total
            ),
            "task_id_isolation_accuracy": _percentage(
                isolation_passed, case_count
            ),
            "critical_field_completeness_rate": _percentage(
                field_passed, field_total
            ),
            "indicator_expectation_match_rate": _percentage(
                indicator_passed, indicator_total
            ),
            "expected_evidence_hit_rate": _percentage(
                evidence_passed, evidence_total
            ),
        },
        "metric_counts": {
            "reports": {"passed": report_passed, "total": case_count},
            "citations": {"passed": citation_passed, "total": citation_total},
            "isolated_tasks": {"passed": isolation_passed, "total": case_count},
            "critical_fields": {"passed": field_passed, "total": field_total},
            "indicator_expectations": {
                "passed": indicator_passed,
                "total": indicator_total,
            },
            "expected_evidence": {
                "passed": evidence_passed,
                "total": evidence_total,
            },
        },
        "cases": results,
    }


def _nullable_number_matches(actual: Any, expected: Any, tolerance: float) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return abs(float(actual) - float(expected)) <= tolerance


def _compact_text(value: Any) -> str:
    """Ignore PDF layout whitespace when matching a human-readable phrase."""

    return "".join(str(value).split())


def _percentage(passed: int, total: int) -> float:
    return round((passed / total) * 100, 2) if total else 100.0


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the KeHeng technology pipeline on fixed cases."
    )
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument(
        "--mode",
        choices=("rule", "llm", "fallback"),
        default="rule",
        help="Extraction mode; llm uses the deterministic offline Mock provider.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = MockLLMProvider() if args.mode == "llm" else None
    if args.runtime_root:
        result = asyncio.run(
            evaluate_cases(
                args.case_root,
                args.runtime_root,
                agent_mode=args.mode,
                llm_provider=provider,
            )
        )
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            result = asyncio.run(
                evaluate_cases(
                    args.case_root,
                    temporary,
                    agent_mode=args.mode,
                    llm_provider=provider,
                )
            )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["passed_case_count"] == result["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
