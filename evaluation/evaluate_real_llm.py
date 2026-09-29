"""Evaluate a real OpenAI-compatible Technology Agent without changing scoring.

The script intentionally has no provider fallback. It requires an explicit
endpoint, model and API key through environment variables and records every
provider or contract failure in the output JSON.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm import (  # noqa: E402
    LLMExtractionRequest,
    LLMProviderError,
    OpenAICompatibleProvider,
)
from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisArtifacts,
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from evaluation.evaluate_pipeline import (  # noqa: E402
    DEFAULT_CASE_ROOT,
    EvaluationCase,
    _constraint_errors,
    load_cases,
)


EVIDENCE_PATTERN = re.compile(r"\[([A-Z]\d+)\]")
TRL_PATTERN = re.compile(r"\bTRL\s*[-:]?\s*(\d+)\b", re.IGNORECASE)
INDICATOR_IDS = (
    "technical_autonomy",
    "innovation_capability",
    "intellectual_property",
    "technical_maturity",
)
FUTURE_MARKERS = (
    "计划量产",
    "预计量产",
    "拟量产",
    "正在研发",
    "尚未规模化",
    "尚未完成验证",
    "待验证",
)


class RealAPITransport:
    """Small stdlib-only Chat Completions transport with response telemetry."""

    def __init__(self, endpoint: str, api_key: str, timeout: float, temperature: float) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.json_parse_success = False
        self.last_error: str | None = None

    async def __call__(
        self, _endpoint: str, model: str, request: LLMExtractionRequest
    ) -> Mapping[str, Any]:
        payload = {
            "model": model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.prompt},
                {"role": "user", "content": _render_user_context(request)},
            ],
        }
        try:
            response = await asyncio.to_thread(self._request, payload)
            content = _extract_message_content(response)
            parsed = _parse_json_content(content)
            _preflight_extraction(parsed, request)
            self.json_parse_success = True
            self.last_error = None
            return parsed
        except Exception as exc:
            self.json_parse_success = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, LLMProviderError):
                raise
            raise LLMProviderError("real LLM response was not valid extraction JSON") from exc

    def _request(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMProviderError(
                f"real LLM HTTP error {exc.code}: {body}"
            ) from exc
        except URLError as exc:
            raise LLMProviderError(f"real LLM connection error: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("real LLM HTTP response was not JSON") from exc
        if not isinstance(parsed, Mapping):
            raise LLMProviderError("real LLM HTTP response must be a JSON object")
        return parsed


async def evaluate_real_llm(
    case_root: str | Path = DEFAULT_CASE_ROOT,
    runtime_root: str | Path | None = None,
    runs_per_case: int = 3,
    endpoint: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Run rule baseline and real LLM stability checks on fixed cases."""

    if runs_per_case < 1:
        raise ValueError("runs_per_case must be positive")
    resolved_endpoint = endpoint or os.getenv("KEHENG_LLM_ENDPOINT")
    resolved_model = model or os.getenv("KEHENG_LLM_MODEL")
    resolved_key = api_key or os.getenv("KEHENG_LLM_API_KEY")
    if not resolved_endpoint or not resolved_model or not resolved_key:
        raise RuntimeError(
            "Real LLM evaluation requires KEHENG_LLM_ENDPOINT, "
            "KEHENG_LLM_MODEL and KEHENG_LLM_API_KEY"
        )
    resolved_timeout = timeout or float(os.getenv("KEHENG_LLM_TIMEOUT_SECONDS", "120"))
    resolved_temperature = (
        temperature
        if temperature is not None
        else float(os.getenv("KEHENG_LLM_TEMPERATURE", "0"))
    )

    cases = load_cases(case_root)
    if runtime_root is None:
        with tempfile.TemporaryDirectory(
            prefix="keheng-v065-real-llm-", ignore_cleanup_errors=True
        ) as temporary:
            return await _run(
                cases,
                Path(temporary),
                runs_per_case,
                resolved_endpoint,
                resolved_model,
                resolved_key,
                resolved_timeout,
                resolved_temperature,
            )
    return await _run(
        cases,
        Path(runtime_root).resolve(),
        runs_per_case,
        resolved_endpoint,
        resolved_model,
        resolved_key,
        resolved_timeout,
        resolved_temperature,
    )


async def _run(
    cases: list[EvaluationCase],
    runtime_root: Path,
    runs_per_case: int,
    endpoint: str,
    model: str,
    api_key: str,
    timeout: float,
    temperature: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        rule_artifact, rule_error = await _run_once(
            case,
            runtime_root / "rule" / case.case_id,
            agent_mode="rule",
            provider=None,
            task_suffix="rule",
        )
        rule_snapshot = (
            _snapshot(case, rule_artifact, json_parse_success=True)
            if rule_artifact is not None
            else _failed_snapshot(rule_error or "rule baseline failed")
        )

        llm_runs: list[dict[str, Any]] = []
        for run_number in range(1, runs_per_case + 1):
            transport = RealAPITransport(endpoint, api_key, timeout, temperature)
            provider = OpenAICompatibleProvider(endpoint, model, transport=transport)
            artifact, error = await _run_once(
                case,
                runtime_root / "llm" / case.case_id / f"run-{run_number}",
                agent_mode="llm",
                provider=provider,
                task_suffix=f"llm-{run_number}",
            )
            if artifact is None:
                failure = error or transport.last_error or "unknown LLM failure"
                if transport.last_error and transport.last_error not in failure:
                    failure = f"{failure}; provider_detail={transport.last_error}"
                llm_runs.append(
                    {
                        "run": run_number,
                        "json_parse_success": transport.json_parse_success,
                        "indicator_complete": False,
                        "evidence_reference_completeness_rate": 0.0,
                        "evaluation_success": False,
                        "report_generated": False,
                        "errors": [failure],
                    }
                )
            else:
                snapshot = _snapshot(
                    case,
                    artifact,
                    json_parse_success=transport.json_parse_success,
                )
                snapshot["run"] = run_number
                llm_runs.append(snapshot)

        results.append(
            {
                "case_id": case.case_id,
                "enterprise_name": case.enterprise_name,
                "rule": rule_snapshot,
                "llm_runs": llm_runs,
                "stability": _stability(llm_runs),
                "rule_vs_llm": _compare_rule_llm(rule_snapshot, llm_runs),
                "failed_checks": sorted(
                    {
                        error
                        for item in llm_runs
                        for error in item.get("errors", [])
                    }
                ),
            }
        )
    return {
        "schema_version": "1.0",
        "evaluation": "v0.6.5-real-llm",
        "runs_per_case": runs_per_case,
        "config": {
            "endpoint": endpoint,
            "model": model,
            "temperature": temperature,
            "timeout_seconds": timeout,
            "api_key_configured": bool(api_key),
        },
        "cases": results,
        "summary": _summary(results),
    }


async def _run_once(
    case: EvaluationCase,
    runtime_root: Path,
    agent_mode: str,
    provider: OpenAICompatibleProvider | None,
    task_suffix: str,
) -> tuple[TechnologyAnalysisArtifacts | None, str | None]:
    service = TechnologyAssessmentService(
        project_root=ROOT,
        runtime_root=runtime_root,
        agent_mode=agent_mode,
        llm_provider=provider,
    )
    try:
        return (
            await service.run_with_artifacts(
                TechnologyAnalysisInput(
                    task_id=f"real-{case.case_id}-{task_suffix}",
                    enterprise_name=case.enterprise_name,
                    pdf_path=case.pdf_path,
                    original_file_name=case.pdf_path.name,
                )
            ),
            None,
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _snapshot(
    case: EvaluationCase,
    artifacts: TechnologyAnalysisArtifacts,
    json_parse_success: bool,
) -> dict[str, Any]:
    analysis = artifacts.technology_analysis.model_dump(mode="json")
    evaluation = artifacts.evaluation_result.model_dump(mode="json")
    report = artifacts.report.model_dump(mode="json")
    evidence_check = _evidence_checks(case, analysis, evaluation, report)
    errors = [
        *evidence_check["errors"],
        *_constraint_errors(analysis, case.expected_indicators),
    ]
    return {
        "json_parse_success": json_parse_success,
        "indicator_complete": _indicator_complete(analysis),
        "evidence_reference_completeness_rate": evidence_check["rate"],
        "evaluation_success": evaluation.get("technology_score") is not None
        or all(value is None for value in (evaluation.get("dimension_scores") or {}).values()),
        "report_generated": bool(report.get("title")),
        "technology_score": evaluation.get("technology_score"),
        "dimension_scores": evaluation.get("dimension_scores"),
        "indicator_scores": {
            key: (value or {}).get("score")
            for key, value in (analysis.get("technology_indicators") or {}).items()
        },
        "evidence": _evidence_snapshot(analysis),
        "trl": _extract_trl(analysis),
        "errors": sorted(set(errors)),
    }


def _failed_snapshot(error: str) -> dict[str, Any]:
    return {
        "json_parse_success": False,
        "indicator_complete": False,
        "evidence_reference_completeness_rate": 0.0,
        "evaluation_success": False,
        "report_generated": False,
        "technology_score": None,
        "dimension_scores": {},
        "indicator_scores": {},
        "evidence": {},
        "trl": None,
        "errors": [error],
    }


def _indicator_complete(analysis: Mapping[str, Any]) -> bool:
    indicators = analysis.get("technology_indicators") or {}
    return set(indicators) == set(INDICATOR_IDS) and all(
        isinstance(indicators.get(identifier), Mapping)
        for identifier in INDICATOR_IDS
    )


def _evidence_checks(
    case: EvaluationCase,
    analysis: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_items = analysis.get("evidence") or []
    valid_ids = {str(item.get("evidence_id")) for item in evidence_items}
    errors: list[str] = []
    required = 0
    supported = 0

    def check(ids: list[str], label: str, required_for_conclusion: bool) -> None:
        nonlocal required, supported
        if required_for_conclusion:
            required += 1
        unknown = sorted(set(ids) - valid_ids)
        if unknown:
            errors.append(f"invalid E编号 in {label}: {unknown}")
        if required_for_conclusion and ids and not unknown:
            supported += 1
        if required_for_conclusion and not ids:
            errors.append(f"无证据结论: {label}")

    summary_ids = EVIDENCE_PATTERN.findall(str(analysis.get("technology_summary", "")))
    check(summary_ids, "technology_summary", True)
    for identifier in INDICATOR_IDS:
        item = (analysis.get("technology_indicators") or {}).get(identifier) or {}
        check(
            [str(value) for value in item.get("evidence") or []],
            f"technology_indicators.{identifier}",
            item.get("score") is not None,
        )
    for field in ("strengths", "risks"):
        for index, content in enumerate(analysis.get(field) or []):
            check(
                EVIDENCE_PATTERN.findall(str(content)),
                f"{field}[{index}]",
                True,
            )
    report_ids = {
        str(item.get("evidence_id"))
        for item in report.get("references") or []
        if item.get("evidence_id")
    }
    if report_ids - valid_ids:
        errors.append(f"invalid E编号 in report.references: {sorted(report_ids - valid_ids)}")
    for mapping in evaluation.get("evidence_mapping") or []:
        check(
            [str(value) for value in mapping.get("evidence_ids") or []],
            "evaluation.evidence_mapping",
            True,
        )

    maturity = (analysis.get("technology_indicators") or {}).get("technical_maturity") or {}
    maturity_score = maturity.get("score")
    future_evidence = [
        str(item.get("excerpt", ""))
        for item in evidence_items
        if any(marker in str(item.get("excerpt", "")) for marker in FUTURE_MARKERS)
    ]
    if maturity_score is not None and maturity_score > 25 and future_evidence:
        errors.append("未来语义误判: future/negative evidence received a maturity score above 25")
    return {
        "rate": round((supported / required) * 100, 2) if required else 100.0,
        "errors": errors,
    }


def _evidence_snapshot(analysis: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("evidence_id")): {
            "document_name": item.get("document_name"),
            "page_number": item.get("page_number"),
            "excerpt": item.get("excerpt"),
        }
        for item in analysis.get("evidence") or []
        if item.get("evidence_id")
    }


def _extract_trl(analysis: Mapping[str, Any]) -> int | None:
    maturity = (analysis.get("technology_indicators") or {}).get("technical_maturity") or {}
    match = TRL_PATTERN.search(str(maturity.get("rationale", "")))
    return int(match.group(1)) if match else None


def _stability(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in runs if item.get("report_generated")]
    scores = [item.get("technology_score") for item in successful]
    numeric_scores = [float(score) for score in scores if score is not None]
    trls = [item.get("trl") for item in successful if item.get("trl") is not None]
    return {
        "output_success_rate": round((len(successful) / len(runs)) * 100, 2) if runs else 0.0,
        "score_values": scores,
        "score_delta": round(max(numeric_scores) - min(numeric_scores), 4)
        if numeric_scores
        else None,
        "trl_values": trls,
        "trl_delta": max(trls) - min(trls) if trls else None,
    }


def _compare_rule_llm(rule: Mapping[str, Any], runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    successful = [item for item in runs if item.get("report_generated")]
    if not successful:
        return {
            "indicator_differences": None,
            "score_difference": None,
            "evidence_difference": None,
        }
    indicator_diffs = {
        identifier: sorted(
            {
                (item.get("indicator_scores") or {}).get(identifier)
                for item in successful
            }
            | {(rule.get("indicator_scores") or {}).get(identifier)}
        )
        for identifier in INDICATOR_IDS
    }
    llm_scores = [item.get("technology_score") for item in successful]
    rule_score = rule.get("technology_score")
    score_difference = [
        round(float(score) - float(rule_score), 4)
        if score is not None and rule_score is not None
        else None
        for score in llm_scores
    ]
    rule_evidence = rule.get("evidence") or {}
    llm_evidence = successful[0].get("evidence") or {}
    return {
        "indicator_differences": indicator_diffs,
        "score_difference": score_difference,
        "evidence_difference": {
            "rule_only_evidence_ids": sorted(set(rule_evidence) - set(llm_evidence)),
            "llm_only_evidence_ids": sorted(set(llm_evidence) - set(rule_evidence)),
        },
    }


def _summary(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    runs = [run for case in results for run in case.get("llm_runs", [])]
    return {
        "case_count": len(results),
        "total_llm_runs": len(runs),
        "successful_llm_runs": sum(bool(run.get("report_generated")) for run in runs),
        "failed_checks": sorted(
            {
                error
                for case in results
                for error in case.get("failed_checks", [])
            }
        ),
    }


def _render_user_context(request: LLMExtractionRequest) -> str:
    chunks = [
        {
            "evidence_id": chunk.evidence_id,
            "document_name": chunk.document_name,
            "page_number": chunk.page_number,
            "excerpt": chunk.excerpt,
        }
        for chunk in request.chunks
    ]
    return (
        f"企业名称：{request.enterprise_name}\n"
        "只返回符合提示词契约的 JSON，不要 Markdown，不要最终综合评分。\n"
        f"检索证据：{json.dumps(chunks, ensure_ascii=False)}"
    )


def _extract_message_content(response: Mapping[str, Any]) -> str | Mapping[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("real LLM response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, (str, Mapping)):
        return content
    raise LLMProviderError("real LLM response has no message content")


def _parse_json_content(content: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(content, Mapping):
        return content
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("real LLM message content is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise LLMProviderError("real LLM extraction JSON must be an object")
    return parsed


def _preflight_extraction(
    payload: Mapping[str, Any], request: LLMExtractionRequest
) -> None:
    """Classify obvious evidence-contract failures before the Agent boundary."""

    valid_ids = {chunk.evidence_id for chunk in request.chunks}
    invalid: set[str] = set()
    errors: list[str] = []
    summary_ids = payload.get("summary_evidence_ids") or []
    invalid.update(
        str(item)
        for item in summary_ids
        if str(item) not in valid_ids
    )
    indicators = payload.get("technology_indicators") or {}
    if isinstance(indicators, Mapping):
        for item in indicators.values():
            if not isinstance(item, Mapping):
                continue
            invalid.update(
                str(value)
                for value in item.get("evidence_ids") or []
                if str(value) not in valid_ids
            )
    for field in ("strengths", "risks"):
        for index, finding in enumerate(payload.get(field) or []):
            if isinstance(finding, Mapping):
                invalid.update(
                    str(value)
                    for value in finding.get("evidence_ids") or []
                    if str(value) not in valid_ids
                )
                if finding.get("content") and not finding.get("evidence_ids"):
                    errors.append(f"无证据结论: {field}[{index}]")
    if invalid:
        errors.append(f"invalid E编号: {sorted(invalid)}")
    if payload.get("technology_summary") and not summary_ids:
        errors.append("无证据结论: technology_summary")
    if isinstance(indicators, Mapping):
        for identifier, item in indicators.items():
            if isinstance(item, Mapping) and item.get("score") is not None and not item.get(
                "evidence_ids"
            ):
                errors.append(f"无证据结论: technology_indicators.{identifier}")
    if errors:
        raise LLMProviderError("; ".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run three-case, three-run real OpenAI-compatible LLM evaluation."
    )
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            evaluate_real_llm(
                case_root=args.case_root,
                runtime_root=args.runtime_root,
                runs_per_case=args.runs,
            )
        )
    except Exception as exc:
        print(f"real LLM evaluation not executed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["summary"]["successful_llm_runs"] == result["summary"]["total_llm_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
