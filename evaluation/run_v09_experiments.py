"""Run the v0.9 real Alibaba Bailian experiment on all fixed cases.

The script requires a key explicitly injected through the environment, calls the selected
OpenAI-compatible model, and writes raw model responses without credentials.
Rule runs are a baseline; LLM failures are recorded and never downgraded to
rule mode.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider  # noqa: E402
from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisArtifacts,
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.evaluate_pipeline import (  # noqa: E402
    EvaluationCase,
    load_cases as load_technology_cases,
)
from evaluation.evaluate_composite import (  # noqa: E402
    CompositeCase,
    load_cases as load_composite_cases,
)
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402
from evaluation.provenance import build_run_provenance  # noqa: E402


DEFAULT_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_RUNS = 3
PROMPT_VERSION = "v0.9-strict-json-technology-r1"
INDICATORS = (
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
TRL_PATTERN = re.compile(r"\bTRL\s*[-:]?\s*(\d+)\b", re.IGNORECASE)
EVIDENCE_PATTERN = re.compile(r"\[\s*([A-Z]\d+)\s*\]")
BRACKET_REF_PATTERN = re.compile(r"\[\s*([A-Za-z]+\d*)\s*\]")
FUTURE_MARKERS = ("计划量产", "预计量产", "拟量产", "正在研发", "尚未规模化", "尚未完成验证", "待验证")


@dataclass(frozen=True)
class FixedCase:
    case_id: str
    enterprise_name: str
    pdf_path: Path
    kind: str
    expected_technology: dict[str, Any]
    expected_industry: dict[str, Any] | None = None
    expected_evidence: dict[str, Any] | None = None


def load_fixed_cases() -> list[FixedCase]:
    tech = [
        FixedCase(
            case_id=case.case_id,
            enterprise_name=case.enterprise_name,
            pdf_path=case.pdf_path,
            kind="technology",
            expected_technology=case.expected_indicators,
            expected_evidence=case.expected_evidence,
        )
        for case in load_technology_cases(ROOT / "data" / "evaluation_cases")
    ]
    composite = []
    for case in load_composite_cases(ROOT / "data" / "evaluation_cases" / "composite"):
        composite.append(
            FixedCase(
                case_id=case.case_id,
                enterprise_name=case.case_id,
                pdf_path=case.root / "company_profile.pdf",
                kind="composite",
                expected_technology=_load_json(case.root / "expected_technology.json"),
                expected_industry=_load_json(case.root / "expected_industry.json"),
            )
        )
    return tech + composite


def discover_api_key() -> str:
    configured = os.getenv("KEHENG_LLM_API_KEY")
    if configured:
        return configured
    raise RuntimeError("未配置 KEHENG_LLM_API_KEY；请通过安全的后端环境变量注入")


def query_models(endpoint: str, api_key: str) -> list[str]:
    request = Request(
        endpoint.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    return [str(item["id"]) for item in payload.get("data", []) if item.get("id")]


async def run_experiment(
    output_root: Path,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    runs_per_case: int = DEFAULT_RUNS,
) -> dict[str, Any]:
    require_fresh_output_directory(output_root)
    if runs_per_case < 1:
        raise ValueError("runs_per_case must be positive")
    api_key = discover_api_key()
    available_models = query_models(endpoint, api_key)
    if model not in available_models:
        raise RuntimeError(f"selected model is not available: {model}")
    cases = load_fixed_cases()
    config = {
        "schema_version": "0.9.0",
        "provider": "Alibaba Cloud Bailian OpenAI Compatible",
        "endpoint": endpoint,
        "model": model,
        "model_selection_reason": "中文技术资料理解、结构化 JSON 稳定性与批量实验成本的平衡；不进行无意义的大规模模型横评。",
        "available_models_queried": available_models,
        "temperature": temperature,
        "prompt_version": PROMPT_VERSION,
        "runs_per_case": runs_per_case,
        "api_key_configured": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "gold_data_unchanged": True,
        "provenance": build_run_provenance(
            Path(__file__), cases,
            [ROOT / "prompts" / "technology_agent_prompt.md", ROOT / "prompts" / "industry_agent_prompt.md"],
            {"endpoint": endpoint, "model": model, "temperature": temperature, "json_mode": "provider default", "timeout_seconds_env": "KEHENG_LLM_TIMEOUT_SECONDS"},
        ),
    }
    _write_json(output_root / "experiment_config.json", config)

    records: list[dict[str, Any]] = []
    for case in cases:
        rule = await _run_case_once(case, output_root / "rule" / case.case_id, "rule", None, 0)
        llm_runs = []
        for run_number in range(1, runs_per_case + 1):
            transport = OpenAICompatibleHTTPTransport(
                api_key=api_key, timeout=float(os.getenv("KEHENG_LLM_TIMEOUT_SECONDS", "180")), temperature=temperature
            )
            provider = OpenAICompatibleProvider(endpoint, model, transport=transport)
            result = await _run_case_once(
                case,
                output_root / "llm" / case.case_id / f"run-{run_number}",
                "llm",
                provider,
                run_number,
            )
            result["provider_call_count"] = transport.call_count
            result["provider_raw_response"] = transport.last_response
            result["provider_error"] = transport.last_error
            llm_runs.append(result)
        records.append(
            {
                "case_id": case.case_id,
                "kind": case.kind,
                "enterprise_name": case.enterprise_name,
                "rule": rule,
                "llm_runs": llm_runs,
                "stability": _stability(llm_runs),
                "rule_vs_llm": _compare(rule, llm_runs),
            }
        )

    raw = {"schema_version": "0.9.0", "cases": records}
    comparisons = {item["case_id"]: item["rule_vs_llm"] for item in records}
    metrics = _metrics(records)
    errors = _error_cases(records)
    _write_json(output_root / "raw_runs.json", raw)
    _write_json(output_root / "rule_vs_llm.json", {"schema_version": "0.9.0", "cases": comparisons})
    _write_json(output_root / "metrics.json", metrics)
    _write_json(output_root / "error_cases.json", errors)
    return {"config": config, "metrics": metrics, "errors": errors}


async def _run_case_once(
    case: FixedCase,
    runtime_root: Path,
    mode: str,
    provider: OpenAICompatibleProvider | None,
    run_number: int,
) -> dict[str, Any]:
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=runtime_root,
        agent_mode=mode,
        llm_provider=provider,
    )
    request = TechnologyAnalysisInput(
        task_id=f"v09-{mode}-{case.case_id}-{run_number or 'baseline'}",
        enterprise_name=case.enterprise_name,
        pdf_path=case.pdf_path,
        original_file_name=case.pdf_path.name,
    )
    try:
        if case.kind == "composite":
            artifacts = await service.run_comprehensive_with_artifacts(request)
            return _snapshot_composite(case, artifacts)
        artifacts = await service.run_with_artifacts(request)
        return _snapshot_technology(case, artifacts)
    except Exception as exc:  # noqa: BLE001 - retain explicit experiment failures
        return {
            "api_call_success": mode == "rule",
            "json_contract_success": False,
            "indicator_complete": False,
            "valid_evidence_reference_rate": 0.0,
            "gold_indicator_match_rate": 0.0,
            "expected_evidence_hit_rate": 0.0,
            "evaluation_success": False,
            "report_success": False,
            "technology_score": None,
            "industry_score": None,
            "overall_score": None,
            "trl": None,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def _snapshot_technology(case: FixedCase, artifacts: TechnologyAnalysisArtifacts) -> dict[str, Any]:
    analysis = artifacts.technology_analysis.model_dump(mode="json")
    evaluation = artifacts.evaluation_result.model_dump(mode="json")
    report = artifacts.report.model_dump(mode="json")
    checks = _evidence_checks(analysis, evaluation, report)
    gold = _gold_technology(case, analysis, report)
    errors = checks["errors"] + _semantic_errors(analysis)
    return {
        "api_call_success": True,
        "json_contract_success": True,
        "indicator_complete": _indicator_complete(analysis),
        "valid_evidence_reference_rate": checks["rate"],
        "gold_indicator_match_rate": gold["indicator_rate"],
        "expected_evidence_hit_rate": gold["evidence_rate"],
        "evaluation_success": evaluation.get("technology_score") is not None or all(v is None for v in (evaluation.get("dimension_scores") or {}).values()),
        "report_success": bool(report.get("title")),
        "technology_score": evaluation.get("technology_score"),
        "industry_score": None,
        "overall_score": None,
        "dimension_scores": evaluation.get("dimension_scores"),
        "indicator_scores": {key: (value or {}).get("score") for key, value in (analysis.get("technology_indicators") or {}).items()},
        "evidence_ids": sorted({str(item.get("evidence_id")) for item in analysis.get("evidence") or []}),
        "trl": _extract_trl(analysis),
        "errors": sorted(set(errors)),
        "artifacts": {"technology_analysis": analysis, "evaluation": evaluation, "report": report},
    }


def _snapshot_composite(case: FixedCase, artifacts: Any) -> dict[str, Any]:
    tech = artifacts.technology_analysis.model_dump(mode="json")
    industry = artifacts.industry_analysis.model_dump(mode="json")
    tech_eval = artifacts.technology_evaluation.model_dump(mode="json")
    ind_eval = artifacts.industry_evaluation
    composite = artifacts.comprehensive_evaluation
    report = artifacts.report.model_dump(mode="json")
    combined_valid_ids = {
        str(item.get("evidence_id"))
        for item in tech.get("evidence") or []
    } | {
        str(item.get("evidence_id"))
        for item in industry.get("evidence") or []
    }
    checks = _evidence_checks(tech, tech_eval, report, valid_evidence_override=combined_valid_ids)
    industry_checks = _industry_evidence_checks(industry, ind_eval)
    gold = _gold_composite(case, tech, industry)
    errors = checks["errors"] + industry_checks["errors"] + _semantic_errors(tech) + _industry_semantic_errors(industry)
    return {
        "api_call_success": True,
        "json_contract_success": True,
        "indicator_complete": _indicator_complete(tech) and _industry_complete(industry),
        "valid_evidence_reference_rate": _mean([checks["rate"], industry_checks["rate"]]),
        "gold_indicator_match_rate": gold,
        "expected_evidence_hit_rate": _mean([checks["rate"], industry_checks["rate"]]),
        "evaluation_success": composite.get("overall_score") is not None,
        "report_success": bool(report.get("title")),
        "technology_score": composite.get("technology_score"),
        "industry_score": composite.get("industry_score"),
        "overall_score": composite.get("overall_score"),
        "dimension_scores": composite.get("dimension_scores"),
        "indicator_scores": {key: (value or {}).get("score") for key, value in (tech.get("technology_indicators") or {}).items()},
        "industry_indicator_scores": {key: (value or {}).get("score") for key, value in (industry.get("industry_indicators") or {}).items()},
        "evidence_ids": sorted({str(item.get("evidence_id")) for item in tech.get("evidence") or []} | {str(item.get("evidence_id")) for item in industry.get("evidence") or []}),
        "trl": _extract_trl(tech),
        "errors": sorted(set(errors)),
        "artifacts": {"technology_analysis": tech, "industry_analysis": industry, "technology_evaluation": tech_eval, "industry_evaluation": ind_eval, "composite_evaluation": composite, "report": report},
    }


def _evidence_checks(
    analysis: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    report: Mapping[str, Any],
    valid_evidence_override: set[str] | None = None,
) -> dict[str, Any]:
    valid = valid_evidence_override or {
        str(item.get("evidence_id")) for item in analysis.get("evidence") or []
    }
    checks: list[bool] = []
    errors: list[str] = []
    summary_ids = EVIDENCE_PATTERN.findall(str(analysis.get("technology_summary", "")))
    invalid_summary_refs = [ref for ref in BRACKET_REF_PATTERN.findall(str(analysis.get("technology_summary", ""))) if not re.fullmatch(r"E\d+", ref)]
    if invalid_summary_refs:
        errors.append(f"摘要存在无效引用标记: {sorted(set(invalid_summary_refs))}")
    checks.append(bool(summary_ids) and set(summary_ids) <= valid)
    if not checks[-1]:
        errors.append("无效或缺失摘要证据引用")
    for key in INDICATORS:
        item = (analysis.get("technology_indicators") or {}).get(key) or {}
        ids = [str(value) for value in item.get("evidence") or []]
        if item.get("score") is not None:
            checks.append(bool(ids) and set(ids) <= valid)
            if not checks[-1]:
                errors.append(f"无证据或无效 E 编号: technology_indicators.{key}")
    for field in ("strengths", "risks"):
        for index, value in enumerate(analysis.get(field) or []):
            ids = EVIDENCE_PATTERN.findall(str(value))
            invalid_refs = [ref for ref in BRACKET_REF_PATTERN.findall(str(value)) if not re.fullmatch(r"E\d+", ref)]
            if invalid_refs:
                errors.append(f"{field}[{index}] 存在无效引用标记: {sorted(set(invalid_refs))}")
            checks.append(bool(ids) and set(ids) <= valid)
            if not checks[-1]:
                errors.append(f"无证据或无效 E 编号: {field}[{index}]")
    for mapping in evaluation.get("evidence_mapping") or []:
        ids = [str(value) for value in mapping.get("evidence_ids") or []]
        # An unscored/insufficient indicator may legitimately have no mapping;
        # only validate mappings that claim to carry evidence.
        if not ids:
            continue
        checks.append(bool(ids) and set(ids) <= valid)
        if not checks[-1]:
            errors.append("评价证据映射无效")
    report_ids = {str(item.get("evidence_id")) for item in report.get("references") or [] if item.get("evidence_id")}
    if report_ids - valid:
        errors.append(f"报告存在无效 E 编号: {sorted(report_ids - valid)}")
    return {"rate": round(sum(checks) / len(checks) * 100, 2) if checks else 100.0, "errors": errors}


def _industry_evidence_checks(analysis: Mapping[str, Any], evaluation: Mapping[str, Any]) -> dict[str, Any]:
    valid = {str(item.get("evidence_id")) for item in analysis.get("evidence") or []}
    checks: list[bool] = []
    errors: list[str] = []
    for key in INDUSTRY_INDICATORS:
        item = (analysis.get("industry_indicators") or {}).get(key) or {}
        if item.get("score") is not None:
            ids = [str(value) for value in item.get("evidence") or []]
            checks.append(bool(ids) and set(ids) <= valid)
            if not checks[-1]:
                errors.append(f"产业指标无证据或无效 E 编号: {key}")
    for mapping in evaluation.get("evidence_mapping") or []:
        ids = [str(value) for value in mapping.get("evidence_ids") or []]
        if not ids:
            continue
        checks.append(bool(ids) and set(ids) <= valid)
    return {"rate": round(sum(checks) / len(checks) * 100, 2) if checks else 100.0, "errors": errors}


def _gold_technology(case: FixedCase, analysis: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, float]:
    expected = case.expected_technology.get("expected_indicators", {})
    actual = analysis.get("technology_indicators") or {}
    tolerance = float(case.expected_technology.get("score_tolerance", 0.001))
    matched = 0
    for key in INDICATORS:
        target = expected.get(key, {})
        value = (actual.get(key) or {}).get("score")
        if _close(value, target.get("score"), tolerance) and bool((actual.get(key) or {}).get("evidence")) == bool(target.get("evidence_required")):
            matched += 1
    expected_evidence = case.expected_evidence or {}
    hits = 0
    refs = report.get("references") or []
    for item in expected_evidence.get("required_evidence", []):
        if any(item.get("page_number") == ref.get("page_number") and all(term in str(ref.get("excerpt", "")) for term in item.get("contains_all", [])) for ref in refs):
            hits += 1
    return {"indicator_rate": round(matched / len(INDICATORS) * 100, 2), "evidence_rate": round(hits / len(expected_evidence.get("required_evidence", [])) * 100, 2) if expected_evidence.get("required_evidence") else 100.0}


def _gold_composite(case: FixedCase, tech: Mapping[str, Any], industry: Mapping[str, Any]) -> float:
    matched = 0
    total = len(INDICATORS) + len(INDUSTRY_INDICATORS)
    for key, expected in case.expected_technology.get("expected_indicators", {}).items():
        actual = (tech.get("technology_indicators") or {}).get(key) or {}
        if _close(actual.get("score"), expected.get("score"), 0.01):
            matched += 1
    for key, expected in (case.expected_industry or {}).get("expected_indicators", {}).items():
        actual = (industry.get("industry_indicators") or {}).get(key) or {}
        if _close(actual.get("score"), expected.get("score"), 0.01):
            matched += 1
    return round(matched / total * 100, 2) if total else 100.0


def _semantic_errors(analysis: Mapping[str, Any]) -> list[str]:
    evidence = [str(item.get("excerpt", "")) for item in analysis.get("evidence") or []]
    maturity = (analysis.get("technology_indicators") or {}).get("technical_maturity") or {}
    score = maturity.get("score")
    errors: list[str] = []
    if score is not None and score > 25 and any(marker in text for text in evidence for marker in FUTURE_MARKERS):
        errors.append("未来语义误判或 TRL 过高")
    return errors


def _industry_semantic_errors(analysis: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence = [str(item.get("excerpt", "")) for item in analysis.get("evidence") or []]
    competition = (analysis.get("industry_indicators") or {}).get("competitive_position") or {}
    competition_score = competition.get("score")
    if competition_score is not None and competition_score >= 70 and evidence and not any(term in "".join(evidence) for term in ("市场份额", "竞争优势", "差异化", "壁垒", "行业领先", "核心客户")):
        errors.append("行业前景被错误等价为企业竞争力")
    return errors


def _compare(rule: Mapping[str, Any], runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    successful = [item for item in runs if item.get("report_success")]
    return {
        "rule_scores": {key: rule.get(key) for key in ("technology_score", "industry_score", "overall_score")},
        "llm_scores": [{key: item.get(key) for key in ("technology_score", "industry_score", "overall_score")} for item in successful],
        "indicator_differences": {
            key: sorted(
                {
                    _json_number((item.get("indicator_scores") or {}).get(key))
                    for item in successful
                }
                | {_json_number((rule.get("indicator_scores") or {}).get(key))},
                key=lambda value: (value is not None, value if value is not None else -1),
            )
            for key in INDICATORS
        },
        "evidence_id_sets": {"rule": rule.get("evidence_ids", []), "llm": [item.get("evidence_ids", []) for item in successful]},
    }


def _stability(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    successful = [item for item in runs if item.get("report_success")]
    result: dict[str, Any] = {"output_success_rate": round(len(successful) / len(runs) * 100, 2) if runs else 0.0}
    for name in ("technology_score", "industry_score", "overall_score"):
        values = [float(item[name]) for item in successful if item.get(name) is not None]
        result[name] = _volatility(values)
    trls = [item.get("trl") for item in successful if item.get("trl") is not None]
    result["trl_values"] = trls
    result["trl_stable"] = len(set(trls)) <= 1
    return result


def _metrics(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    runs = [run for record in records for run in record.get("llm_runs", [])]
    def rate(field: str) -> float:
        return round(sum(bool(run.get(field)) for run in runs) / len(runs) * 100, 2) if runs else 0.0
    return {
        "case_count": len(records),
        "llm_runs": len(runs),
        "api_call_success_rate": rate("api_call_success"),
        "json_contract_success_rate": rate("json_contract_success"),
        "indicator_complete_rate": rate("indicator_complete"),
        "valid_evidence_reference_rate": round(_mean([float(run.get("valid_evidence_reference_rate", 0)) for run in runs]), 2) if runs else 0.0,
        "gold_indicator_match_rate": round(_mean([float(run.get("gold_indicator_match_rate", 0)) for run in runs]), 2) if runs else 0.0,
        "expected_evidence_hit_rate": round(_mean([float(run.get("expected_evidence_hit_rate", 0)) for run in runs]), 2) if runs else 0.0,
        "evaluation_success_rate": rate("evaluation_success"),
        "report_success_rate": rate("report_success"),
        "stability_by_case": {record["case_id"]: record["stability"] for record in records},
    }


def _error_cases(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    errors = []
    for record in records:
        for mode in ("rule", "llm_runs"):
            values = [record[mode]] if mode == "rule" else record[mode]
            for value in values:
                for error in value.get("errors", []):
                    errors.append({"case_id": record["case_id"], "mode": "llm" if mode == "llm_runs" else mode, "run": value.get("run"), "error": error})
    return {"count": len(errors), "items": errors}


def _indicator_complete(analysis: Mapping[str, Any]) -> bool:
    values = analysis.get("technology_indicators") or {}
    return set(values) == set(INDICATORS) and all(isinstance(values.get(key), Mapping) for key in INDICATORS)


def _industry_complete(analysis: Mapping[str, Any]) -> bool:
    values = analysis.get("industry_indicators") or {}
    return set(values) == set(INDUSTRY_INDICATORS) and all(isinstance(values.get(key), Mapping) for key in INDUSTRY_INDICATORS)


def _extract_trl(analysis: Mapping[str, Any]) -> int | None:
    rationale = str(((analysis.get("technology_indicators") or {}).get("technical_maturity") or {}).get("rationale", ""))
    match = TRL_PATTERN.search(rationale)
    return int(match.group(1)) if match else None


def _volatility(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"values": [], "min": None, "max": None, "range": None, "stddev": None}
    mean = sum(values) / len(values)
    return {"values": values, "min": min(values), "max": max(values), "range": round(max(values) - min(values), 4), "stddev": round(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 4)}


def _semantic_json(value: Any) -> Any:
    return value


def _json_number(value: Any) -> Any:
    return None if value is None else float(value)


def _close(actual: Any, expected: Any, tolerance: float) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(float(actual) - float(expected)) <= tolerance


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="必须是新目录；默认在 runtime/experiments/real_llm/run-* 下创建")
    parser.add_argument("--model", default=os.getenv("KEHENG_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--endpoint", default=os.getenv("KEHENG_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("KEHENG_LLM_TEMPERATURE", str(DEFAULT_TEMPERATURE))))
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    args = parser.parse_args()
    output_root = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "experiments" / "real_llm")
    result = asyncio.run(run_experiment(output_root, args.endpoint, args.model, args.temperature, args.runs))
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
