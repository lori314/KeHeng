"""Run the v1.1 structured-output repair experiment on all fixed cases.

The script keeps the v1.0 gold data and rule baseline untouched.  A repair is
allowed only once and is recorded separately from the model's first response.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider  # noqa: E402
from app.services.analysis_service import TechnologyAnalysisInput, TechnologyAssessmentService  # noqa: E402
from evaluation.run_v09_experiments import (  # noqa: E402
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    INDUSTRY_INDICATORS,
    INDICATORS,
    _evidence_checks,
    _industry_evidence_checks,
    _industry_semantic_errors,
    _semantic_errors,
    _extract_trl,
    discover_api_key,
    load_fixed_cases,
    query_models,
)
from evaluation.run_v10_experiments import (  # noqa: E402
    _snapshot_composite_v10,
    _snapshot_technology_v10,
)
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402
from evaluation.provenance import build_run_provenance  # noqa: E402


PROMPT_VERSION = "v1.1-structured-json-technology"
INDUSTRY_PROMPT_VERSION = "v1.1-structured-json-industry"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_observations(transport: OpenAICompatibleHTTPTransport) -> list[dict[str, Any]]:
    history = getattr(transport, "observation_history", None) or []
    return [dict(item) for item in history] or [dict(transport.last_observation)]


def _run_status(observations: list[dict[str, Any]]) -> dict[str, Any]:
    first_valid = bool(observations) and all(bool(item.get("first_response_valid")) for item in observations)
    repaired = any(bool(item.get("repair_triggered")) for item in observations)
    repair_success = repaired and all(
        bool(item.get("repair_success")) for item in observations if item.get("repair_triggered")
    )
    final_success = bool(observations) and all(item.get("final_status") in {"first_pass_success", "repair_success"} for item in observations)
    return {
        "first_response_valid": first_valid,
        "repair_triggered": repaired,
        "repair_success": repair_success,
        "final_status": "first_pass_success" if first_valid else "repair_success" if final_success else "failed",
        "llm_call_count": len(observations),
    }


async def _run_once(case: Any, output_root: Path, run_number: int, api_key: str) -> dict[str, Any]:
    transport = OpenAICompatibleHTTPTransport(
        api_key=api_key,
        timeout=float(os.getenv("KEHENG_LLM_TIMEOUT_SECONDS", "180")),
        temperature=0.0,
        max_retries=2,
        json_mode=os.getenv("KEHENG_LLM_JSON_MODE", "1").lower() not in {"0", "false", "no"},
    )
    provider = OpenAICompatibleProvider(
        DEFAULT_ENDPOINT,
        DEFAULT_MODEL,
        transport=transport,
        repair_enabled=True,
    )
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=output_root,
        agent_mode="llm",
        industry_mode="llm" if case.kind == "composite" else "rule",
        llm_provider=provider,
    )
    request = TechnologyAnalysisInput(
        task_id=f"v11-llm-{case.case_id}-{run_number}",
        enterprise_name=case.enterprise_name,
        pdf_path=case.pdf_path,
        original_file_name=case.pdf_path.name,
    )
    observations: list[dict[str, Any]] = []
    try:
        if case.kind == "composite":
            artifacts = await service.run_comprehensive_with_artifacts(request)
            payload = _snapshot_composite_v10(case, artifacts)
        else:
            artifacts = await service.run_with_artifacts(request)
            payload = _snapshot_technology_v10(case, artifacts)
        observations = _call_observations(transport)
        payload["failure_category"] = None
    except Exception as exc:  # retain explicit failures and no fallback
        observations = _call_observations(transport)
        category = getattr(exc, "category", None) or (observations[-1].get("error_category") if observations else None) or "other"
        payload = {
            "api_call_success": bool(observations) and all(item.get("eventual_success") for item in observations),
            "json_contract_success": False,
            "indicator_complete": False,
            "valid_evidence_reference_rate": 0.0,
            "expected_evidence_hit_rate": 0.0,
            "evaluation_success": False,
            "report_success": False,
            "technology_score": None,
            "industry_score": None,
            "overall_score": None,
            "indicator_scores": {},
            "industry_indicator_scores": {},
            "errors": [f"{type(exc).__name__}: {exc}"],
            "failure_category": category,
        }
    status = _run_status(observations)
    artifacts = payload.get("artifacts") or {}
    technology_artifact = artifacts.get("technology_analysis") if isinstance(artifacts, dict) else None
    if isinstance(technology_artifact, dict):
        payload["trl"] = _extract_trl(technology_artifact)
    payload.update(
        {
            "run": run_number,
            **status,
            "observability": observations,
            "provider_raw_response": transport.last_response,
            "provider_raw_content_excerpt": (transport.last_content[:24000] if isinstance(transport.last_content, str) else transport.last_content),
            "provider_response_history": transport.response_history,
            "provider_content_history": transport.content_history,
        }
    )
    return payload


async def _rule_baseline(case: Any, root: Path) -> dict[str, Any]:
    service = TechnologyAssessmentService(project_root=ROOT, runtime_root=root, agent_mode="rule", industry_mode="rule")
    request = TechnologyAnalysisInput(task_id=f"v11-rule-{case.case_id}", enterprise_name=case.enterprise_name, pdf_path=case.pdf_path, original_file_name=case.pdf_path.name)
    try:
        artifacts = await (service.run_comprehensive_with_artifacts(request) if case.kind == "composite" else service.run_with_artifacts(request))
        if case.kind == "composite":
            result = artifacts.comprehensive_evaluation
            return {"technology_score": result.get("technology_score"), "industry_score": result.get("industry_score"), "overall_score": result.get("overall_score")}
        return {"technology_score": artifacts.evaluation_result.technology_score, "industry_score": None, "overall_score": None}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _metrics(records: list[dict[str, Any]], cases: list[Any]) -> dict[str, Any]:
    runs = [run for record in records for run in record["llm_runs"]]
    case_by_id = {case.case_id: case for case in cases}
    def rate(field: str, values: list[dict[str, Any]]) -> float:
        return round(sum(bool(item.get(field)) for item in values) / len(values) * 100, 2) if values else 0.0
    final = [item for item in runs if item.get("final_status") in {"first_pass_success", "repair_success"}]
    repair_attempts = [item for item in runs if item.get("repair_triggered")]
    scores = lambda key: [float(item[key]) for item in final if item.get(key) is not None]
    def summary(key: str) -> dict[str, Any]:
        values = scores(key)
        return {"n": len(values), "min": min(values) if values else None, "max": max(values) if values else None, "range": round(max(values) - min(values), 4) if values else None}
    trl_values = [int(item["trl"]) for item in runs if item.get("trl") is not None]
    def quality(domain: str) -> dict[str, Any]:
        errors: list[float] = []
        exact = within_5 = within_10 = 0
        for record in records:
            expected_root = case_by_id[record["case_id"]].expected_technology if domain == "technology" else case_by_id[record["case_id"]].expected_industry
            expected = (expected_root or {}).get("expected_indicators", {})
            for run in record["llm_runs"]:
                if run.get("final_status") not in {"first_pass_success", "repair_success"}:
                    continue
                actual = run.get("indicator_scores" if domain == "technology" else "industry_indicator_scores") or {}
                for key, target in expected.items():
                    if target.get("score") is not None and actual.get(key) is not None:
                        error = abs(float(actual[key]) - float(target["score"]))
                        errors.append(error)
                        exact += error == 0
                        within_5 += error <= 5
                        within_10 += error <= 10
        return {"n": len(errors), "mae": round(sum(errors) / len(errors), 4) if errors else None, "exact_match_rate": round(exact / len(errors) * 100, 2) if errors else 0.0, "within_5_rate": round(within_5 / len(errors) * 100, 2) if errors else 0.0, "within_10_rate": round(within_10 / len(errors) * 100, 2) if errors else 0.0}
    return {
        "total_runs": len(runs),
        "first_pass_success_rate": rate("first_response_valid", runs),
        "repair_trigger_rate": rate("repair_triggered", runs),
        "repair_success_rate": round(sum(bool(item.get("repair_success")) for item in repair_attempts) / len(repair_attempts) * 100, 2) if repair_attempts else 0.0,
        "final_success_rate": round(len(final) / len(runs) * 100, 2) if runs else 0.0,
        "json_contract_success_rate": rate("json_contract_success", runs),
        "indicator_complete_rate": rate("indicator_complete", final),
        "valid_evidence_rate": round((sum(float(item.get("valid_evidence_reference_rate", 0)) for item in final) / len(final)) if final and max(float(item.get("valid_evidence_reference_rate", 0)) for item in final) > 1 else (sum(float(item.get("valid_evidence_reference_rate", 0)) for item in final) / len(final) * 100 if final else 0.0), 2),
        "evaluation_success_rate": rate("evaluation_success", final),
        "report_success_rate": rate("report_success", final),
        "technology_score_stability": summary("technology_score"),
        "industry_score_stability": summary("industry_score"),
        "overall_score_stability": summary("overall_score"),
        "trl_risk_run_count": sum(1 for item in runs if any("TRL" in str(error) or "未来语义" in str(error) for error in item.get("errors", []))),
        "trl_stability": {"n": len(trl_values), "values": sorted(set(trl_values)), "min": min(trl_values) if trl_values else None, "max": max(trl_values) if trl_values else None, "range": max(trl_values) - min(trl_values) if trl_values else None},
        "technology_indicator_quality": quality("technology"),
        "industry_indicator_quality": quality("industry"),
    }


async def run_experiment(output_root: Path, runs_per_case: int = 5) -> dict[str, Any]:
    require_fresh_output_directory(output_root)
    api_key = discover_api_key()
    available = query_models(DEFAULT_ENDPOINT, api_key)
    if DEFAULT_MODEL not in available:
        raise RuntimeError(f"selected model is not available: {DEFAULT_MODEL}")
    cases = load_fixed_cases()
    json_mode = os.getenv("KEHENG_LLM_JSON_MODE", "1").lower() not in {"0", "false", "no"}
    config = {
        "schema_version": "1.1.0",
        "provider": "Alibaba Cloud Bailian OpenAI Compatible",
        "endpoint": DEFAULT_ENDPOINT,
        "model": DEFAULT_MODEL,
        "available_models_queried": available,
        "temperature": 0.0,
        "json_mode_enabled": json_mode,
        "response_format": {"type": "json_object"} if json_mode else None,
        "json_mode_verified_by": "successful qwen3.7-plus structured-output calls",
        "technology_prompt_version": PROMPT_VERSION,
        "industry_prompt_version": INDUSTRY_PROMPT_VERSION,
        "repair_policy": "at_most_one_format_only_repair; no_rule_fallback",
        "runs_per_case": runs_per_case,
        "gold_data_unchanged": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provenance": build_run_provenance(
            Path(__file__), cases,
            [ROOT / "prompts" / "technology_agent_prompt.md", ROOT / "prompts" / "industry_agent_prompt.md"],
            {"endpoint": DEFAULT_ENDPOINT, "model": DEFAULT_MODEL, "temperature": 0.0, "json_mode": json_mode, "repair_enabled": True, "max_retries": 2, "timeout_seconds_env": "KEHENG_LLM_TIMEOUT_SECONDS"},
        ),
    }
    _write(output_root / "experiment_config.json", config)
    records: list[dict[str, Any]] = []
    for case in cases:
        runs = [await _run_once(case, output_root / "llm" / case.case_id / f"run-{number}", number, api_key) for number in range(1, runs_per_case + 1)]
        rule = await _rule_baseline(case, output_root / "rule" / case.case_id)
        records.append({"case_id": case.case_id, "kind": case.kind, "rule": rule, "llm_runs": runs})
    raw = {"schema_version": "1.1.0", "cases": records}
    _write(output_root / "raw_runs.json", raw)
    all_runs = [run for record in records for run in record["llm_runs"]]
    _write(output_root / "metrics.json", _metrics(records, cases))
    _write(output_root / "rule_vs_llm.json", {record["case_id"]: {"rule": record["rule"], "llm": [{**{key: run.get(key) for key in ("technology_score", "industry_score", "overall_score", "final_status")}, "indicator_scores": run.get("indicator_scores", {}), "industry_indicator_scores": run.get("industry_indicator_scores", {}), "evidence_ids": run.get("evidence_ids", [])} for run in record["llm_runs"]]} for record in records})
    _write(output_root / "error_cases.json", {"items": [{"case_id": record["case_id"], "run": run.get("run"), "category": run.get("failure_category"), "errors": run.get("errors", []), "repair": {key: run.get(key) for key in ("first_response_valid", "repair_triggered", "repair_success", "final_status")}, "observability": run.get("observability", [])} for record in records for run in record["llm_runs"] if run.get("failure_category") or run.get("errors")]})
    return {"config": config, "metrics": _metrics(records, cases), "failed_runs": sum(1 for run in all_runs if not run.get("json_contract_success"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="必须是新目录；默认在 runtime/experiments/v11/run-* 下创建")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    output_root = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "experiments" / "v11")
    result = asyncio.run(run_experiment(output_root, args.runs))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
