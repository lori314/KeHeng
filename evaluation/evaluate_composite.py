"""Run the v0.8 technology-plus-industry evaluation experiment.

The script intentionally calls the same opt-in service used by the application.
It only measures outputs; it does not change either Agent or scoring logic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.composite_scoring import ComprehensiveEvaluationEngine  # noqa: E402


DEFAULT_CASE_ROOT = PROJECT_ROOT / "data" / "evaluation_cases" / "composite"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "evaluation" / "composite"
CASE_IDS = (
    "case_tech_industry_high",
    "case_tech_high_industry_low",
    "case_tech_low_industry_high",
)
TECH_INDICATORS = (
    "technical_autonomy",
    "innovation_capability",
    "intellectual_property",
    "technical_maturity",
)
INDUSTRY_INDICATORS = (
    "market_potential",
    "industry_growth",
    "competitive_position",
    "policy_environment",
)


@dataclass(frozen=True)
class CompositeCase:
    case_id: str
    root: Path
    expected_technology: dict[str, Any]
    expected_industry: dict[str, Any]
    expected_composite: dict[str, Any]


def load_cases(case_root: Path = DEFAULT_CASE_ROOT) -> list[CompositeCase]:
    cases: list[CompositeCase] = []
    for case_id in CASE_IDS:
        root = (case_root / case_id).resolve()
        if not (root / "company_profile.pdf").is_file():
            raise FileNotFoundError(f"缺少 PDF: {root / 'company_profile.pdf'}")
        payloads = {
            name: _load_json(root / name)
            for name in (
                "expected_technology.json",
                "expected_industry.json",
                "expected_composite.json",
            )
        }
        for payload in payloads.values():
            if payload.get("case_id") != case_id:
                raise ValueError(f"{case_id} 的金标准 case_id 不一致")
        cases.append(
            CompositeCase(
                case_id=case_id,
                root=root,
                expected_technology=payloads["expected_technology.json"],
                expected_industry=payloads["expected_industry.json"],
                expected_composite=payloads["expected_composite.json"],
            )
        )
    return cases


async def evaluate_composite(
    case_root: Path = DEFAULT_CASE_ROOT,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> dict[str, Any]:
    cases = load_cases(case_root)
    service = TechnologyAssessmentService(
        project_root=PROJECT_ROOT,
        runtime_root=runtime_root,
        agent_mode="rule",
    )
    results: list[dict[str, Any]] = []
    scores: dict[str, dict[str, float | None]] = {}

    for case in cases:
        task_id = f"v08_{case.case_id}"
        item: dict[str, Any] = {"case_id": case.case_id, "task_id": task_id}
        try:
            artifacts = await service.run_comprehensive_with_artifacts(
                TechnologyAnalysisInput(
                    task_id=task_id,
                    enterprise_name=case.case_id,
                    pdf_path=case.root / "company_profile.pdf",
                    original_file_name="company_profile.pdf",
                )
            )
            tech_payload = artifacts.technology_evaluation.model_dump(mode="python")
            industry_payload = artifacts.industry_evaluation
            composite_payload = artifacts.comprehensive_evaluation
            tech_actual = {
                key: _score(artifacts.technology_analysis.technology_indicators, key)
                for key in TECH_INDICATORS
            }
            industry_actual = {
                key: _score(artifacts.industry_analysis.industry_indicators, key)
                for key in INDUSTRY_INDICATORS
            }
            tech_match = _indicator_match(
                tech_actual, case.expected_technology.get("expected_indicators", {})
            )
            industry_match = _indicator_match(
                industry_actual, case.expected_industry.get("expected_indicators", {})
            )
            evidence = _evidence_completeness(artifacts, TECH_INDICATORS, INDUSTRY_INDICATORS)
            tolerance = float(case.expected_composite.get("score_tolerance", 0.01))
            score_match = all(
                _close(composite_payload.get(name), case.expected_composite.get(name), tolerance)
                for name in ("technology_score", "industry_score", "overall_score")
            )
            dimension_match = _mapping_match(
                composite_payload.get("dimension_scores", {}),
                case.expected_composite.get("dimension_scores", {}),
                tolerance,
            )
            report_ok = bool(artifacts.report.references) and bool(artifacts.report.overall_score is not None)
            item.update(
                {
                    "technology_score": tech_payload.get("technology_score"),
                    "industry_score": industry_payload.get("industry_score"),
                    "overall_score": composite_payload.get("overall_score"),
                    "dimension_scores": composite_payload.get("dimension_scores"),
                    "technology_indicators": tech_actual,
                    "industry_indicators": industry_actual,
                    "indicator_match": {
                        "technology": tech_match,
                        "industry": industry_match,
                    },
                    "evidence_completeness": evidence,
                    "report_generated": report_ok,
                    "score_match": score_match,
                    "dimension_match": dimension_match,
                    "passed": score_match and dimension_match and report_ok and evidence["rate"] == 1.0,
                }
            )
            scores[case.case_id] = {
                "technology": composite_payload.get("technology_score"),
                "industry": composite_payload.get("industry_score"),
            }
        except Exception as exc:  # noqa: BLE001 - retain case-level failure diagnostics
            item.update({"passed": False, "error": f"{type(exc).__name__}: {exc}"})
        results.append(item)

    sensitivity = _sensitivity_analysis(scores)
    summary = {
        "case_count": len(results),
        "passed_cases": sum(bool(item.get("passed")) for item in results),
        "report_generation_rate": _rate(
            sum(bool(item.get("report_generated")) for item in results), len(results)
        ),
        "evidence_completeness_rate": _rate(
            sum(1 for item in results if item.get("evidence_completeness", {}).get("rate") == 1.0),
            len(results),
        ),
    }
    return {
        "schema_version": "0.8.0",
        "mode": "rule",
        "summary": summary,
        "cases": results,
        "sensitivity_analysis": sensitivity,
    }


def _sensitivity_analysis(scores: dict[str, dict[str, float | None]]) -> dict[str, Any]:
    scenarios = (0.8, 0.6, 0.4, 0.2)
    rows: list[dict[str, Any]] = []
    for technology_weight in scenarios:
        engine = ComprehensiveEvaluationEngine(
            {"technology": technology_weight, "industry": round(1 - technology_weight, 10)},
            config_version="sensitivity-0.8",
        )
        ranked: list[dict[str, Any]] = []
        for case_id, domains in scores.items():
            result = engine.evaluate(
                {"technology_score": domains["technology"]},
                {"industry_score": domains["industry"]},
            )
            ranked.append({"case_id": case_id, "overall_score": result.overall_score})
        ranked.sort(key=lambda item: (item["overall_score"] is not None, item["overall_score"] or -1), reverse=True)
        rows.append(
            {
                "weights": {"technology": technology_weight, "industry": round(1 - technology_weight, 2)},
                "ranking": [item["case_id"] for item in ranked],
                "scores": ranked,
            }
        )
    return {
        "scenarios": rows,
        "ranking_changes": [
            {
                "from": rows[index - 1]["weights"],
                "to": row["weights"],
                "changed": rows[index - 1]["ranking"] != row["ranking"],
            }
            for index, row in enumerate(rows)
            if index
        ],
    }


def _score(model: Any, key: str) -> float | None:
    value = getattr(model, key)
    return None if value.score is None else float(value.score)


def _indicator_match(actual: dict[str, float | None], expected: dict[str, Any]) -> dict[str, Any]:
    matched = 0
    for key, target in expected.items():
        if _close(actual.get(key), target.get("score"), float(target.get("tolerance", 0.01))):
            matched += 1
    total = len(expected)
    return {"matched": matched, "total": total, "rate": _rate(matched, total)}


def _mapping_match(actual: dict[str, Any], expected: dict[str, Any], tolerance: float) -> bool:
    return all(_close(actual.get(key), value, tolerance) for key, value in expected.items())


def _evidence_completeness(artifacts: Any, tech_keys: tuple[str, ...], industry_keys: tuple[str, ...]) -> dict[str, Any]:
    checks: list[bool] = []
    tech_evidence_ids = {item.evidence_id for item in artifacts.technology_analysis.evidence}
    industry_evidence_ids = {item.evidence_id for item in artifacts.industry_analysis.evidence}
    for model, keys, valid_ids in (
        (artifacts.technology_analysis.technology_indicators, tech_keys, tech_evidence_ids),
        (artifacts.industry_analysis.industry_indicators, industry_keys, industry_evidence_ids),
    ):
        for key in keys:
            assessment = getattr(model, key)
            if assessment.score is None:
                continue
            checks.append(bool(assessment.evidence) and set(assessment.evidence) <= valid_ids)
    return {"passed": sum(checks), "total": len(checks), "rate": _rate(sum(checks), len(checks))}


def _close(actual: Any, expected: Any, tolerance: float) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(float(actual) - float(expected)) <= tolerance


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _score_payload(value: Any) -> Any:
    return value.model_dump(mode="python") if hasattr(value, "model_dump") else value


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = asyncio.run(evaluate_composite(args.case_root, args.runtime_root))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["summary"]["passed_cases"] == payload["summary"]["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
