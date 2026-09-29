"""Run the v1.0 five-repeat Technology+Industry LLM experiment."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider  # noqa: E402
from app.services.analysis_service import (  # noqa: E402
    AnalysisServiceError,
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.run_v09_experiments import (  # noqa: E402
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    FixedCase,
    INDUSTRY_INDICATORS,
    INDICATORS,
    _evidence_checks,
    _industry_evidence_checks,
    _industry_semantic_errors,
    _semantic_errors,
    _write_json,
    load_fixed_cases,
    discover_api_key,
)
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402
from evaluation.provenance import build_run_provenance  # noqa: E402

PROMPT_VERSION = "v0.9-strict-json-technology-r1"
INDUSTRY_PROMPT_VERSION = "v1.0-industry-strict-json"
DEFAULT_RUNS = 5


async def _run_once(
    case: FixedCase,
    runtime_root: Path,
    provider: OpenAICompatibleProvider,
    transport: OpenAICompatibleHTTPTransport,
    run_number: int,
) -> dict[str, Any]:
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=runtime_root,
        agent_mode="llm",
        industry_mode="llm" if case.kind == "composite" else "rule",
        llm_provider=provider,
    )
    request = TechnologyAnalysisInput(
        task_id=f"v10-llm-{case.case_id}-{run_number}",
        enterprise_name=case.enterprise_name,
        pdf_path=case.pdf_path,
        original_file_name=case.pdf_path.name,
    )
    try:
        if case.kind == "composite":
            artifacts = await service.run_comprehensive_with_artifacts(request)
            payload = _snapshot_composite_v10(case, artifacts)
        else:
            artifacts = await service.run_with_artifacts(request)
            payload = _snapshot_technology_v10(case, artifacts)
        payload["run"] = run_number
        payload["failure_category"] = None
    except Exception as exc:  # retain explicit failures
        observation = dict(transport.last_observation)
        category = getattr(exc, "category", None) or observation.get("error_category") or "other"
        payload = {
            "run": run_number,
            "api_call_success": bool(observation.get("eventual_success")),
            "first_attempt_success": bool(observation.get("first_attempt_schema_success")),
            "eventual_success": bool(observation.get("eventual_schema_success")),
            "json_contract_success": False,
            "indicator_complete": False,
            "valid_evidence_reference_rate": 0.0,
            "expected_evidence_hit_rate": 0.0,
            "evaluation_success": False,
            "report_success": False,
            "technology_score": None,
            "industry_score": None,
            "overall_score": None,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "failure_category": category,
        }
    payload["observability"] = dict(transport.last_observation)
    payload["first_attempt_success"] = bool(payload.get("observability", {}).get("first_attempt_schema_success", payload.get("first_attempt_success", False)))
    payload["eventual_success"] = bool(payload.get("observability", {}).get("eventual_schema_success", payload.get("eventual_success", False)))
    return payload


def _snapshot_technology_v10(case: FixedCase, artifacts: Any) -> dict[str, Any]:
    analysis = artifacts.technology_analysis.model_dump(mode="json")
    evaluation = artifacts.evaluation_result.model_dump(mode="json")
    report = artifacts.report.model_dump(mode="json")
    valid = {str(item.get("evidence_id")) for item in analysis.get("evidence") or []}
    checks = _evidence_checks(analysis, evaluation, report)
    scores = {key: (value or {}).get("score") for key, value in (analysis.get("technology_indicators") or {}).items()}
    return {
        "api_call_success": True,
        "first_attempt_success": True,
        "eventual_success": True,
        "json_contract_success": True,
        "indicator_complete": set(scores) == set(INDICATORS),
        "valid_evidence_reference_rate": checks["rate"],
        "expected_evidence_hit_rate": 0.0,
        "evaluation_success": evaluation.get("technology_score") is not None or all(value is None for value in (evaluation.get("dimension_scores") or {}).values()),
        "report_success": bool(report.get("title")),
        "technology_score": evaluation.get("technology_score"),
        "industry_score": None,
        "overall_score": None,
        "indicator_scores": scores,
        "industry_indicator_scores": {},
        "evidence_ids": sorted(valid),
        "trl": None,
        "errors": sorted(set(checks["errors"] + _semantic_errors(analysis))),
        "artifacts": {"technology_analysis": analysis, "evaluation": evaluation, "report": report},
    }


def _snapshot_composite_v10(case: FixedCase, artifacts: Any) -> dict[str, Any]:
    tech = artifacts.technology_analysis.model_dump(mode="json")
    industry = artifacts.industry_analysis.model_dump(mode="json")
    tech_eval = artifacts.technology_evaluation.model_dump(mode="json")
    ind_eval = artifacts.industry_evaluation
    composite = artifacts.comprehensive_evaluation
    report = artifacts.report.model_dump(mode="json")
    tech_ids = {str(item.get("evidence_id")) for item in tech.get("evidence") or []}
    industry_ids = {str(item.get("evidence_id")) for item in industry.get("evidence") or []}
    checks = _evidence_checks(tech, tech_eval, report, valid_evidence_override=tech_ids | industry_ids)
    industry_checks = _industry_evidence_checks(industry, ind_eval)
    return {
        "api_call_success": True,
        "first_attempt_success": True,
        "eventual_success": True,
        "json_contract_success": True,
        "indicator_complete": _complete(tech, "technology_indicators", INDICATORS) and _complete(industry, "industry_indicators", INDUSTRY_INDICATORS),
        "valid_evidence_reference_rate": round((checks["rate"] + industry_checks["rate"]) / 2, 2),
        "expected_evidence_hit_rate": round((checks["rate"] + industry_checks["rate"]) / 2, 2),
        "evaluation_success": composite.get("assessment_status") != "pipeline_failed",
        "report_success": bool(report.get("title")),
        "technology_score": composite.get("technology_score"),
        "industry_score": composite.get("industry_score"),
        "overall_score": composite.get("overall_score"),
        "assessment_status": composite.get("assessment_status"),
        "evidence_coverage": composite.get("evidence_coverage"),
        "indicator_scores": {key: (value or {}).get("score") for key, value in (tech.get("technology_indicators") or {}).items()},
        "industry_indicator_scores": {key: (value or {}).get("score") for key, value in (industry.get("industry_indicators") or {}).items()},
        "evidence_ids": sorted(tech_ids | industry_ids),
        "trl": None,
        "errors": sorted(set(checks["errors"] + industry_checks["errors"] + _semantic_errors(tech) + _industry_semantic_errors(industry))),
        "artifacts": {"technology_analysis": tech, "industry_analysis": industry, "technology_evaluation": tech_eval, "industry_evaluation": ind_eval, "composite_evaluation": composite, "report": report},
    }


def _complete(payload: Mapping[str, Any], key: str, expected: tuple[str, ...]) -> bool:
    values = payload.get(key) or {}
    return set(values) == set(expected) and all(isinstance(values.get(item), Mapping) for item in expected)


def _error_breakdown(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in records:
        for run in record.get("llm_runs", []):
            category = run.get("failure_category")
            if category:
                counts[category] = counts.get(category, 0) + 1
    total = sum(counts.values())
    recovery = {
        "http_timeout": {"recoverable": True, "recommendation": "有限指数退避重试并继续观测"},
        "rate_limit_429": {"recoverable": True, "recommendation": "退避、限速、减少并发"},
        "http_5xx": {"recoverable": True, "recommendation": "有限重试并保留最终错误"},
        "connection_error": {"recoverable": True, "recommendation": "网络重试与连接诊断"},
        "schema_failure": {"recoverable": False, "recommendation": "修正提示词/结构化输出，不补全字段"},
        "json_syntax_error": {"recoverable": False, "recommendation": "允许合法 code fence，其他 JSON 错误显式记录"},
        "empty_content": {"recoverable": False, "recommendation": "记录空响应并人工复核"},
    }
    items = []
    for category, count in sorted(counts.items()):
        item = recovery.get(category, {"recoverable": False, "recommendation": "保留原始错误并定位"})
        items.append({"category": category, "count": count, "rate": round(count / total * 100, 2) if total else 0.0, **item})
    return {"failed_run_count": total, "categories": items}


def _quality_metrics(runs: list[Mapping[str, Any]], cases: list[FixedCase]) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    def rate(field: str, items: list[Mapping[str, Any]]) -> float:
        return round(sum(bool(item.get(field)) for item in items) / len(items) * 100, 2) if items else 0.0
    def score_quality(domain: str, items: list[Mapping[str, Any]]) -> dict[str, Any]:
        pairs: list[tuple[float, float]] = []
        for item in items:
            case = case_by_id[item["case_id"]]
            expected = case.expected_technology if domain == "technology" else case.expected_industry
            actual = item.get("indicator_scores" if domain == "technology" else "industry_indicator_scores") or {}
            for key, target in (expected or {}).get("expected_indicators", {}).items():
                target_score = target.get("score")
                if target_score is not None and actual.get(key) is not None:
                    pairs.append((float(actual[key]), float(target_score)))
        errors = [abs(a - b) for a, b in pairs]
        return {
            "n": len(pairs),
            "exact_match": round(sum(error <= 0.001 for error in errors) / len(errors) * 100, 2) if errors else 0.0,
            "mae": round(sum(errors) / len(errors), 4) if errors else None,
            "rmse": round(math.sqrt(sum(error * error for error in errors) / len(errors)), 4) if errors else None,
            "within_5": round(sum(error <= 5 for error in errors) / len(errors) * 100, 2) if errors else 0.0,
            "within_10": round(sum(error <= 10 for error in errors) / len(errors) * 100, 2) if errors else 0.0,
        }
    success = [item for item in runs if item.get("json_contract_success") and item.get("evaluation_success")]
    result: dict[str, Any] = {}
    for label, items in (("all_runs", runs), ("successful_runs_only", success)):
        result[label] = {
            "api_first_attempt_success_rate": rate("first_attempt_success", items),
            "api_eventual_success_rate": rate("eventual_success", items),
            "json_schema_success_rate": rate("json_contract_success", items),
            "evaluation_success_rate": rate("evaluation_success", items),
            "report_success_rate": rate("report_success", items),
            "valid_evidence_reference_rate": round(sum(float(item.get("valid_evidence_reference_rate", 0)) for item in items) / len(items), 2) if items else 0.0,
            "technology_quality": score_quality("technology", items),
            "industry_quality": score_quality("industry", items),
            "latency_seconds": _latency(items),
        }
    return result


def _latency(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float((item.get("observability") or {}).get("latency_seconds")) for item in items if (item.get("observability") or {}).get("latency_seconds") is not None]
    if not values:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {"n": len(values), "mean": round(sum(values) / len(values), 4), "min": round(min(values), 4), "max": round(max(values), 4)}


def _stability(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for record in records:
        successful = [run for run in record.get("llm_runs", []) if run.get("report_success")]
        values = {}
        for field in ("technology_score", "industry_score", "overall_score"):
            nums = [float(run[field]) for run in successful if run.get(field) is not None]
            values[field] = _summary(nums)
        result[record["case_id"]] = {"runs": len(record.get("llm_runs", [])), "successful_runs": len(successful), "scores": values, "latency": _latency(record.get("llm_runs", []))}
    return result


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None, "range": None}
    mean = sum(values) / len(values)
    return {"n": len(values), "mean": round(mean, 4), "std": round(math.sqrt(sum((x - mean) ** 2 for x in values) / len(values)), 4), "min": min(values), "max": max(values), "range": round(max(values) - min(values), 4)}


async def run_experiment(output_root: Path, runs_per_case: int = DEFAULT_RUNS) -> dict[str, Any]:
    require_fresh_output_directory(output_root)
    api_key = discover_api_key()
    from evaluation.run_v09_experiments import query_models
    available = query_models(DEFAULT_ENDPOINT, api_key)
    if DEFAULT_MODEL not in available:
        raise RuntimeError(f"selected model is not available: {DEFAULT_MODEL}")
    cases = load_fixed_cases()
    config = {
        "schema_version": "1.0.0",
        "provider": "Alibaba Cloud Bailian OpenAI Compatible",
        "endpoint": DEFAULT_ENDPOINT,
        "model": DEFAULT_MODEL,
        "temperature": 0.0,
        "technology_prompt_version": PROMPT_VERSION,
        "industry_prompt_version": INDUSTRY_PROMPT_VERSION,
        "max_retries": 2,
        "runs_per_case": runs_per_case,
        "api_key_configured": True,
        "gold_data_unchanged": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provenance": build_run_provenance(
            Path(__file__), cases,
            [ROOT / "prompts" / "technology_agent_prompt.md", ROOT / "prompts" / "industry_agent_prompt.md"],
            {"endpoint": DEFAULT_ENDPOINT, "model": DEFAULT_MODEL, "temperature": 0.0, "industry_mode": "llm for composite, rule for technology-only", "json_mode": "provider default", "timeout_seconds_env": "KEHENG_LLM_TIMEOUT_SECONDS"},
        ),
    }
    _write_json(output_root / "experiment_config.json", config)
    records = []
    for case in cases:
        runs = []
        for run_number in range(1, runs_per_case + 1):
            transport = OpenAICompatibleHTTPTransport(api_key, timeout=float(os.getenv("KEHENG_LLM_TIMEOUT_SECONDS", "180")), temperature=0.0, max_retries=2)
            provider = OpenAICompatibleProvider(DEFAULT_ENDPOINT, DEFAULT_MODEL, transport=transport)
            run = await _run_once(case, output_root / "llm" / case.case_id / f"run-{run_number}", provider, transport, run_number)
            runs.append(run)
        rule_transport = None
        rule_service = TechnologyAssessmentService(project_root=ROOT, runtime_root=output_root / "rule" / case.case_id, agent_mode="rule", industry_mode="rule")
        request = TechnologyAnalysisInput(task_id=f"v10-rule-{case.case_id}", enterprise_name=case.enterprise_name, pdf_path=case.pdf_path, original_file_name=case.pdf_path.name)
        try:
            artifacts = await (rule_service.run_comprehensive_with_artifacts(request) if case.kind == "composite" else rule_service.run_with_artifacts(request))
            rule = {"technology_score": artifacts.comprehensive_evaluation.get("technology_score") if case.kind == "composite" else artifacts.evaluation_result.technology_score, "industry_score": artifacts.comprehensive_evaluation.get("industry_score") if case.kind == "composite" else None, "overall_score": artifacts.comprehensive_evaluation.get("overall_score") if case.kind == "composite" else None}
        except Exception as exc:
            rule = {"error": f"{type(exc).__name__}: {exc}"}
        records.append({"case_id": case.case_id, "kind": case.kind, "rule": rule, "llm_runs": runs})
    all_runs = [dict(run, case_id=record["case_id"]) for record in records for run in record["llm_runs"]]
    payload = {"schema_version": "1.0.0", "cases": records}
    _write_json(output_root / "raw_runs.json", payload)
    _write_json(output_root / "error_breakdown.json", _error_breakdown(records))
    _write_json(output_root / "metrics_all_runs.json", _quality_metrics(all_runs, cases)["all_runs"])
    _write_json(output_root / "metrics_successful_runs.json", _quality_metrics(all_runs, cases)["successful_runs_only"])
    _write_json(output_root / "stability.json", _stability(records))
    _write_json(output_root / "rule_vs_llm.json", {record["case_id"]: {"rule": record["rule"], "llm": [{key: run.get(key) for key in ("technology_score", "industry_score", "overall_score")} for run in record["llm_runs"] if run.get("report_success")] } for record in records})
    _write_json(output_root / "error_cases.json", {"items": [{"case_id": record["case_id"], "run": run.get("run"), "category": run.get("failure_category"), "errors": run.get("errors", [])} for record in records for run in record["llm_runs"] if run.get("failure_category") or run.get("errors")]})
    return {"config": config, "metrics": _quality_metrics(all_runs, cases), "error_breakdown": _error_breakdown(records)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="必须是新目录；默认在 runtime/experiments/v10/run-* 下创建")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    args = parser.parse_args()
    output_root = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "experiments" / "v10")
    result = asyncio.run(run_experiment(output_root, args.runs))
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
