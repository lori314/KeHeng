"""Prepare or execute the small same-core hash-versus-BM25 model comparison."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings  # noqa: E402
from app.llm.api_model import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider  # noqa: E402
from app.services.analysis_service import AnalysisServiceError, TechnologyAnalysisInput, TechnologyAssessmentService  # noqa: E402
from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402

COMPANIES = {"smic", "nio", "estun", "cambricon", "siasun", "catl"}
CASE_PATH = ROOT / "evaluation" / "retrieval_eval_cases.json"
CONFIRMATION_DEFAULT = ROOT / "data" / "real_cases" / "iteration03_evidence_confirmations.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def selected_cases(companies: list[str]) -> list[dict]:
    cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if case["company_id"] in companies]


def verify_confirmation(companies: list[str], confirmation_path: Path) -> tuple[bool, list[str]]:
    cases = selected_cases(companies)
    required = {case["case_id"] for case in cases}
    if not confirmation_path.is_file():
        return False, sorted(required)
    value = json.loads(confirmation_path.read_text(encoding="utf-8"))
    confirmed = {row.get("case_id") for row in value.get("confirmations", []) if row.get("status") == "human_confirmed" and row.get("reviewer")}
    missing = sorted(required - confirmed)
    return not missing, missing


def freeze_manifest(companies: list[str]) -> dict:
    """Hash the explicit current code, prompt, evaluation and source snapshot."""
    paths = [
        Path(__file__), ROOT / "backend" / "app" / "services" / "analysis_service.py",
        ROOT / "backend" / "app" / "agents" / "technology_agent.py", ROOT / "backend" / "app" / "agents" / "industry_agent.py",
        ROOT / "backend" / "app" / "rag" / "document_parser.py", ROOT / "backend" / "app" / "rag" / "knowledge_base.py",
        ROOT / "backend" / "app" / "rag" / "bm25.py", ROOT / "backend" / "app" / "rag" / "query_definitions.py",
        ROOT / "backend" / "app" / "llm" / "api_model.py", ROOT / "evaluation" / "retrieval_eval_cases.json",
        ROOT / "evaluation" / "retrieval_eval_cases_v2.json", ROOT / "evaluation" / "retrieval_indicator_definitions.json",
        ROOT / "evaluation" / "indicators.yaml", ROOT / "evaluation" / "weights.yaml",
        ROOT / "evaluation" / "industry" / "indicators.yaml", ROOT / "evaluation" / "industry" / "weights.yaml",
        ROOT / "evaluation" / "composite_weights.yaml", ROOT / "prompts" / "technology_agent_prompt.md",
        ROOT / "prompts" / "industry_agent_prompt.md",
    ]
    for company in companies:
        paths += [ROOT / "data" / "real_cases" / company / "manifest.json", ROOT / "data" / "real_cases" / company / "sources" / "annual_report.pdf"]
    files = {str(path.relative_to(ROOT)): sha(path) for path in sorted(set(paths)) if path.is_file()}
    encoded = json.dumps(files, sort_keys=True).encode("utf-8")
    settings = get_settings()
    return {
        "algorithm": "sha256", "git_commit": None, "git_commit_note": "no Git commit available; explicit source-content hash used",
        "files": files, "source_fingerprint_sha256": hashlib.sha256(encoded).hexdigest(),
        "companies": companies, "analysis_entry": "TechnologyAssessmentService.run_product",
        "retrieval_pair": {"hash": "hash+shared-generic-bilingual-query", "bm25": "BM25+the-same-shared-generic-bilingual-query"},
        "fixed_controls": {"parser": {"chunk_size": 520, "chunk_overlap": 80}, "scoring_config_sha256": scoring_hash(), "http_retries": 0, "max_format_repairs_per_module": 1, "max_primary_calls": 8, "max_total_http_requests": 16},
        "model_config_presence": {"KEHENG_LLM_ENDPOINT": bool(settings.llm_endpoint), "KEHENG_LLM_MODEL": bool(settings.llm_model), "KEHENG_LLM_API_KEY": bool(settings.llm_api_key)},
        "model_name": settings.llm_model or None,
        "endpoint": safe_endpoint(settings.llm_endpoint) if settings.llm_endpoint else None,
        "secret_values_recorded": False,
        "formal_accuracy_eligible": False,
        "label_provenance": "candidate_unconfirmed + AI text review; no human gold asserted",
    }


async def execute(companies: list[str], output_root: Path, confirmation_path: Path) -> dict:
    require_fresh_output_directory(output_root)
    settings = get_settings()
    freeze = freeze_manifest(companies)
    (output_root / "iteration04_frozen_config.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        missing = [name for name, present in freeze["model_config_presence"].items() if not present]
        failure = {
            "schema_version": "1.1.0", "exploratory": True, "formal_accuracy_eligible": False,
            "status": "not_started_missing_configuration", "actual_http_requests": 0,
            "missing_configuration_names": missing, "message": "本地配置缺失，未创建 provider、未发送模型请求。",
            "freeze_manifest": "iteration04_frozen_config.json", "freeze_sha256": sha(output_root / "iteration04_frozen_config.json"),
        }
        (output_root / "paired_model_results.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"output_root": str(output_root), **failure}
    confirmed, missing = verify_confirmation(companies, confirmation_path)
    provider = OpenAICompatibleProvider.from_http(settings.llm_endpoint, settings.llm_model, settings.llm_api_key, timeout=settings.llm_timeout_seconds, max_retries=0, repair_enabled=True)
    transport = provider._transport
    results = []
    stop_reason = None
    for company in companies:
        manifest = json.loads((ROOT / "data" / "real_cases" / company / "manifest.json").read_text(encoding="utf-8"))
        pdf = ROOT / "data" / "real_cases" / company / "sources" / "annual_report.pdf"
        enterprise = next((item["enterprise_name"] for item in selected_cases([company])), company)
        input_data = TechnologyAnalysisInput(task_id=f"paired-{output_root.name}-{company}", enterprise_name=enterprise, pdf_path=pdf, original_file_name="annual_report.pdf")
        for retriever in ("hash", "bm25"):
            service = TechnologyAssessmentService(project_root=ROOT, runtime_root=output_root / "work", llm_provider=provider, retrieval_mode=retriever, query_mode="bilingual")
            request_start = len(transport.request_history)
            response_start = len(transport.response_history)
            observation_start = len(transport.observation_history)
            started = datetime.now(timezone.utc).isoformat()
            elapsed_started = time.perf_counter()
            try:
                result = await service.run_product(input_data, "real_model")
                status = result["result_status"]
                modules = result["modules"]
                run_info = result["run_info"]
                model_io = result.get("model_io", {})
                report = result.get("report")
                error = None
            except AnalysisServiceError as exc:
                status, modules, run_info, model_io, report, error = "failed", {}, {}, {}, None, {"code": exc.code, "category": exc.category, "message": exc.message}
            actual_requests = transport.request_history[request_start:]
            raw_responses = transport.response_history[response_start:]
            request_observations = transport.observation_history[observation_start:]
            usage = [item.get("usage", {}) for item in raw_responses if isinstance(item, dict) and isinstance(item.get("usage"), dict)]
            results.append({
                "company_id": company, "source_pdf_sha256": sha(pdf), "source_manifest_sha256": sha(ROOT / "data" / "real_cases" / company / "manifest.json"),
                "retriever": retriever, "query_set": "shared-generic-bilingual",
                "started_at_utc": started, "elapsed_seconds": round(time.perf_counter() - elapsed_started, 4),
                "status": status, "modules": modules, "run_info": run_info,
                "report": report.model_dump(mode="json") if hasattr(report, "model_dump") else report,
                "error": error,
                "actual_model_requests": actual_requests,
                "raw_responses": raw_responses,
                "request_observations": request_observations,
                "usage_summary": {
                    "http_requests": len(actual_requests), "completion_tokens": sum(int(u.get("completion_tokens", 0) or 0) for u in usage),
                    "prompt_tokens": sum(int(u.get("prompt_tokens", 0) or 0) for u in usage),
                    "total_tokens": sum(int(u.get("total_tokens", 0) or 0) for u in usage),
                    "provider_reported_cost": None,
                    "cost_note": "Provider pricing was not available in the response; token usage is recorded without estimating currency cost.",
                },
            })
            recent_categories = [str(x.get("error_category", "")) for x in request_observations]
            recent_statuses = [x.get("http_status_code") for x in request_observations]
            if any(code in {401, 402, 403, 429} for code in recent_statuses) or any(x.get("billing_or_quota_error") for x in request_observations):
                stop_reason = "authentication_billing_or_rate_limit_error"
            elif len(recent_categories) >= 2 and all(category in {"http_5xx", "http_timeout", "connection_error", "other_transport_error"} for category in recent_categories[-2:]):
                stop_reason = "two_consecutive_service_transport_errors"
            # Persist after each paired run so interruption cannot erase completed work.
            checkpoint = {
                "schema_version": "1.1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "offline": False, "exploratory": True,
                "formal_accuracy_eligible": False,
                "label_provenance": "candidate_unconfirmed plus AI text review; no human gold asserted",
                "companies": companies, "paired_primary_call_budget": 8, "max_total_http_requests": 16,
                "actual_http_requests_so_far": transport.call_count,
                "stop_reason": stop_reason,
                "model": {"provider": "openai-compatible", "name": settings.llm_model, "endpoint": safe_endpoint(settings.llm_endpoint), "max_http_retries": 0, "max_format_repairs_per_module": 1},
                "pairing_controls": {"parser": {"chunk_size": 520, "chunk_overlap": 80}, "scoring_config_sha256": scoring_hash(), "shared_analysis_entry": "TechnologyAssessmentService.run_product", "context": "same Agent compaction: at most 8 chunks/18,000 characters per module", "hash_queries": "four shared generic bilingual queries per domain", "bm25_queries": "the same four shared generic bilingual queries per domain"},
                "confirmation_manifest_sha256": sha(confirmation_path) if confirmation_path.is_file() else None,
                "human_confirmations_found": confirmed, "missing_human_confirmations": missing,
                "results": results,
            }
            (output_root / "paired_model_results.json").write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if stop_reason:
                break
        if stop_reason:
            break
    payload = {
        "schema_version": "1.1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(), "offline": False,
        "exploratory": True, "formal_accuracy_eligible": False,
        "label_provenance": "candidate_unconfirmed plus AI text review; no human gold asserted",
        "companies": companies, "paired_primary_call_budget": 8, "max_total_http_requests": 16,
        "actual_http_requests": transport.call_count,
        "stop_reason": stop_reason,
        "model": {"provider": "openai-compatible", "name": settings.llm_model, "endpoint": safe_endpoint(settings.llm_endpoint), "max_http_retries": 0, "max_format_repairs_per_module": 1},
        "pairing_controls": {"parser": {"chunk_size": 520, "chunk_overlap": 80}, "scoring_config_sha256": scoring_hash(), "shared_analysis_entry": "TechnologyAssessmentService.run_product", "context": "same Agent compaction: at most 8 chunks/18,000 characters per module", "hash_queries": "four shared generic bilingual queries per domain", "bm25_queries": "the same four shared generic bilingual queries per domain"},
        "confirmation_manifest_sha256": sha(confirmation_path) if confirmation_path.is_file() else None,
        "human_confirmations_found": confirmed, "missing_human_confirmations": missing,
        "results": results,
    }
    (output_root / "paired_model_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output_root": str(output_root), "primary_calls_budget": 8, "http_request_upper_bound": 16, "actual_runs": len(results)}


def scoring_hash() -> str:
    paths = [ROOT / "evaluation" / "indicators.yaml", ROOT / "evaluation" / "weights.yaml", ROOT / "evaluation" / "industry" / "indicators.yaml", ROOT / "evaluation" / "industry" / "weights.yaml", ROOT / "evaluation" / "composite_weights.yaml"]
    return hashlib.sha256(json.dumps({str(path.relative_to(ROOT)): sha(path) for path in paths}, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", default="nio,estun", help="恰好两家；默认一英一中")
    parser.add_argument("--confirmation-manifest", type=Path, default=CONFIRMATION_DEFAULT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--prepare-only", action="store_true", help="只写不含模型结果的调用计划，不访问模型")
    parser.add_argument("--execute", action="store_true", help="明确发起付费/真实模型调用；须先确认案例证据")
    parser.add_argument("--exploratory", action="store_true", help="允许标签待确认时生成探索性输出；不允许纳入正式准确率，结果显式保留标签来源")
    args = parser.parse_args()
    companies = [part.strip().lower() for part in args.companies.split(",") if part.strip()]
    if len(companies) != 2 or len(set(companies)) != 2 or any(company not in COMPANIES for company in companies):
        parser.error("--companies 必须包含两家不同的已保存企业")
    if args.execute == args.prepare_only:
        parser.error("必须且只能选择 --prepare-only 或 --execute")
    output = args.output_root if args.output_root else new_run_directory(ROOT / "runtime" / "review" / "paired_model")
    if args.prepare_only:
        require_fresh_output_directory(output)
        required = [case["case_id"] for case in selected_cases(companies)]
        plan = {"mode": "prepare_only", "exploratory_mode": True, "formal_accuracy_eligible": False, "label_provenance": "candidate_unconfirmed plus AI text review; no human gold asserted", "model_calls": {"primary": 8, "maximum_http_requests_including_one_repair_each_and_zero_transport_retries": 16}, "companies": companies, "required_human_confirmations_for_formal_accuracy": required, "evidence_confirmations_found": verify_confirmation(companies, args.confirmation_manifest)[0], "executed": False, "next_command": f"python -m evaluation.run_paired_model_experiment --companies {','.join(companies)} --execute --exploratory"}
        (output / "paired_model_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "iteration04_frozen_config.json").write_text(json.dumps(freeze_manifest(companies), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output_root": str(output), **plan}, ensure_ascii=False, indent=2))
        return 0
    if not args.exploratory:
        confirmed, missing = verify_confirmation(companies, args.confirmation_manifest)
        if not confirmed:
            parser.error("正式准确率模式要求人工确认；本轮仅可使用 --exploratory，缺少：" + ", ".join(missing))
    print(json.dumps(asyncio.run(execute(companies, output, args.confirmation_manifest)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
