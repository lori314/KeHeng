"""Offline, API-free recomputation of saved v1.1 raw evaluation records."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evaluation.run_safety import new_run_directory, require_fresh_output_directory  # noqa: E402
from evaluation.run_v09_experiments import INDUSTRY_INDICATORS, INDICATORS, load_fixed_cases  # noqa: E402

RAW_DEFAULT = ROOT / "runtime" / "experiments" / "v11" / "raw_runs.json"
HISTORIC_METRICS = ROOT / "runtime" / "experiments" / "v11" / "metrics.json"
DOMAIN_INDICATORS = {"technology": INDICATORS, "industry": INDUSTRY_INDICATORS}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(paths: list[Path]) -> dict[str, Any]:
    files = [{"path": _display_path(path), "sha256": _sha(path)} for path in sorted(set(paths))]
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"algorithm": "sha256", "scope": "explicit current source/data files; not a historical commit", "files": files, "digest": digest}


def _normal(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _expected(case: Any, domain: str) -> dict[str, Any]:
    return (case.expected_technology if domain == "technology" else case.expected_industry) or {}


def _artifacts(run: dict[str, Any], domain: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    analysis = artifacts.get("technology_analysis" if domain == "technology" else "industry_analysis") or {}
    return artifacts, analysis


def _actual_score(run: dict[str, Any], domain: str, key: str) -> tuple[bool, Any]:
    artifacts, analysis = _artifacts(run, domain)
    field = "technology_indicators" if domain == "technology" else "industry_indicators"
    indicators = analysis.get(field) if isinstance(analysis, dict) else None
    if isinstance(indicators, dict) and key in indicators and isinstance(indicators[key], dict) and "score" in indicators[key]:
        return True, indicators[key]["score"]
    flat_field = "indicator_scores" if domain == "technology" else "industry_indicator_scores"
    flat = run.get(flat_field)
    if isinstance(flat, dict) and key in flat:
        return True, flat[key]
    return False, None


def _gold_null_is_explicit(case: Any, domain: str, key: str) -> bool:
    """Only count null as explicit abstention when the case specification says so."""
    expected = _expected(case, domain).get("expected_indicators", {}).get(key, {})
    if expected.get("score") is not None:
        return False
    if expected.get("evidence_required") is not False:
        return False
    # These checked-in case specifications explicitly define null as not scored.
    # Other future nulls remain unlabelled until they gain a reason/status field.
    readmes = list((ROOT / "data" / "evaluation_cases").rglob(f"{case.case_id}/README.md"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in readmes if p.is_file())
    explicit_markers = ("材料不足", "保持未评分", "专利信息缺失应保持未评分", "预期结果")
    return any(marker in text for marker in explicit_markers)


def _extract_ids(value: Any) -> list[str]:
    return re.findall(r"\bE\d+\b", str(value or ""))


def citation_audit(case: Any, run: dict[str, Any]) -> dict[str, Any]:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    analyses = [("technology", artifacts.get("technology_analysis") or {}), ("industry", artifacts.get("industry_analysis") or {})]
    evidence_by_domain: dict[str, dict[str, dict[str, Any]]] = {}
    evidence_by_task: dict[str, dict[str, Any]] = {}
    structured_total = structured_valid = free_total = free_task_valid = free_bound = 0
    locator_total = locator_valid = 0
    for domain, analysis in analyses:
        evidence = analysis.get("evidence") or []
        evidence_by_domain[domain] = {str(item.get("evidence_id")): item for item in evidence if item.get("evidence_id")}
        evidence_by_task.update(evidence_by_domain[domain])
    # Explicitly structured ID locations: indicator arrays, evaluation mappings and report references.
    for domain, analysis in analyses:
        field = "technology_indicators" if domain == "technology" else "industry_indicators"
        values = analysis.get(field) or {}
        for indicator, item in values.items():
            refs = item.get("evidence") or []
            for ref in refs:
                structured_total += 1
                structured_valid += int(str(ref) in evidence_by_task)
        domain_eval = artifacts.get("evaluation") if domain == "technology" else artifacts.get("industry_evaluation")
        for mapping in (domain_eval or {}).get("evidence_mapping", []) if isinstance(domain_eval, dict) else []:
            for ref in mapping.get("evidence_ids") or []:
                structured_total += 1
                structured_valid += int(str(ref) in evidence_by_task)
    report = artifacts.get("report") or {}
    report_refs = [str(item.get("evidence_id")) for item in report.get("references", []) if isinstance(item, dict) and item.get("evidence_id")]
    for ref in report_refs:
        structured_total += 1
        structured_valid += int(ref in evidence_by_task)

    textual_locations: list[tuple[str, str]] = []
    for domain, analysis in analyses:
        summary_field = "technology_summary" if domain == "technology" else "industry_summary"
        if analysis.get(summary_field):
            textual_locations.append((summary_field, str(analysis[summary_field])))
        indicator_field = "technology_indicators" if domain == "technology" else "industry_indicators"
        for indicator, item in (analysis.get(indicator_field) or {}).items():
            if item.get("rationale"):
                textual_locations.append((f"{indicator_field}.{indicator}", str(item["rationale"])))
        for field_name in ("strengths", "risks"):
            for index, entry in enumerate(analysis.get(field_name) or []):
                textual_locations.append((f"{field_name}[{index}]", str(entry)))
    # Report prose is also free text. Structured references themselves are counted above.
    def add_report_strings(value: Any, path: str = "report") -> None:
        if isinstance(value, str):
            textual_locations.append((path, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                add_report_strings(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key != "references":
                    add_report_strings(item, f"{path}.{key}")
    add_report_strings(report)
    report_ref_set = set(report_refs)
    for location, text in textual_locations:
        for ref in _extract_ids(text):
            free_total += 1
            found = evidence_by_task.get(ref)
            free_task_valid += int(found is not None)
            if found is not None:
                supports = found.get("supports") or []
                bound = location in supports
                if location.startswith("report"):
                    bound = ref in report_ref_set
                free_bound += int(bound)
    # Locator checks are limited to references saved in raw records and original PDFs.
    source = case.pdf_path
    page_text: dict[int, str] = {}
    try:
        import pymupdf
        with pymupdf.open(source) as pdf:
            page_text = {i: _normal(page.get_text("text", sort=True)) for i, page in enumerate(pdf, start=1)}
    except Exception:
        page_text = {}
    seen: set[tuple[str, int, str]] = set()
    for _, refs in analyses:
        for item in refs.get("evidence") or []:
            page = item.get("page_number")
            excerpt = str(item.get("excerpt") or "")
            key = (str(item.get("document_name") or ""), int(page or -1), excerpt)
            if key in seen:
                continue
            seen.add(key)
            locator_total += 1
            normalized = _normal(excerpt)
            if page in page_text and normalized and (normalized in page_text[page] or page_text[page] in normalized):
                locator_valid += 1
    return {
        "structured_reference_ids": {"n": structured_total, "resolvable_in_domain_evidence": structured_valid},
        "free_text_ids": {"n": free_total, "belong_to_task_domain_evidence": free_task_valid, "bound_to_declared_support_location": free_bound},
        "original_text_page_locator": {"n": locator_total, "located": locator_valid, "note": "原文/页码定位，不是语义支持判断"},
    }


def recompute(raw_path: Path, output_root: Path) -> dict[str, Any]:
    require_fresh_output_directory(output_root)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    cases = load_fixed_cases()
    by_id = {case.case_id: case for case in cases}
    case_records = raw.get("cases") if isinstance(raw.get("cases"), list) else []
    details: list[dict[str, Any]] = []
    citation_by_run: list[dict[str, Any]] = []
    for record in case_records:
        case_id = record.get("case_id")
        case = by_id.get(case_id)
        if case is None:
            continue
        runs = record.get("llm_runs") if isinstance(record.get("llm_runs"), list) else []
        for run_index, run in enumerate(runs, start=1):
            for domain in DOMAIN_INDICATORS:
                target = _expected(case, domain).get("expected_indicators", {})
                for indicator in DOMAIN_INDICATORS[domain]:
                    if indicator not in target:
                        continue
                    gold = target[indicator].get("score")
                    present, prediction = _actual_score(run, domain, indicator)
                    row = {
                        "case_id": case_id, "run": run.get("run", run_index), "domain": domain,
                        "indicator": indicator, "gold_score": gold,
                        "gold_status": "scored" if gold is not None else "explicit_insufficient_evidence" if _gold_null_is_explicit(case, domain, indicator) else "unlabelled_null",
                        "prediction_present": present, "predicted_score": prediction,
                        "final_status": run.get("final_status"),
                        "json_contract_success": run.get("json_contract_success"),
                        "error": "; ".join(map(str, run.get("errors") or [])),
                    }
                    if gold is not None and isinstance(prediction, (int, float)) and not isinstance(prediction, bool):
                        row["absolute_error"] = abs(float(prediction) - float(gold))
                    else:
                        row["absolute_error"] = None
                    details.append(row)
            citation_by_run.append({"case_id": case_id, "run": run.get("run", run_index), **citation_audit(case, run)})

    def domain_summary(domain: str) -> dict[str, Any]:
        rows = [row for row in details if row["domain"] == domain]
        gold_scored = [row for row in rows if row["gold_status"] == "scored"]
        paired = [row for row in gold_scored if row["absolute_error"] is not None]
        missing = [row for row in gold_scored if row["predicted_score"] is None]
        insuff = [row for row in rows if row["gold_status"] == "explicit_insufficient_evidence"]
        over_scored = [row for row in insuff if isinstance(row["predicted_score"], (int, float)) and not isinstance(row["predicted_score"], bool)]
        missing_field = [row for row in rows if not row["prediction_present"]]
        failed = [row for row in rows if row["json_contract_success"] is False or row["final_status"] in {"failed", "pipeline_failed"}]
        return {
            "gold_scored_slots": len(gold_scored),
            "score_coverage": {"n_scored": len(paired), "denominator": len(gold_scored), "percent": round(100 * len(paired) / len(gold_scored), 2) if gold_scored else None},
            "conditional_mae": {"n": len(paired), "value": round(sum(row["absolute_error"] for row in paired) / len(paired), 4) if paired else None, "definition": "only gold-scored slots with numeric model output"},
            "gold_scored_but_model_empty": {"n": len(missing), "denominator": len(gold_scored), "percent": round(100 * len(missing) / len(gold_scored), 2) if gold_scored else None},
            "explicit_insufficient_gold_but_model_scored": {"n": len(over_scored), "denominator": len(insuff), "percent": round(100 * len(over_scored) / len(insuff), 2) if insuff else None},
            "output_field_missing": len(missing_field),
            "structured_or_pipeline_failure_slots": len(failed),
            "unlabelled_null_slots": sum(row["gold_status"] == "unlabelled_null" for row in rows),
            "per_indicator": {key: _metric_for_rows([row for row in rows if row["indicator"] == key]) for key in sorted({row["indicator"] for row in rows})},
        }

    metrics = {"schema_version": "2.0.0", "source_raw": _display_path(raw_path), "recomputed_at": datetime.now(timezone.utc).isoformat(), "domains": {domain: domain_summary(domain) for domain in DOMAIN_INDICATORS}}
    metrics["citation_audit"] = _citation_summary(citation_by_run)
    old = json.loads(HISTORIC_METRICS.read_text(encoding="utf-8")) if HISTORIC_METRICS.exists() else None
    current_sources = [Path(__file__), ROOT / "evaluation" / "run_v09_experiments.py", ROOT / "evaluation" / "run_safety.py", raw_path, HISTORIC_METRICS]
    for case in cases:
        current_sources.extend(path for path in (case.expected_technology and ROOT / "data" / "evaluation_cases" / case.case_id / "expected_indicators.json", case.pdf_path) if path and path.exists())
        current_sources.extend(path for path in (ROOT / "data" / "evaluation_cases" / case.case_id / "README.md",) if path.exists())
        current_sources.extend(path for path in (ROOT / "data" / "evaluation_cases" / "composite" / case.case_id).glob("expected_*.json") if path.exists())
    current_sources.extend(path for path in (ROOT / "prompts").rglob("*") if path.is_file())
    manifest = {
        "schema_version": "2.0.0", "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": {"raw_runs.json": _sha(raw_path), "legacy_metrics.json": _sha(HISTORIC_METRICS) if HISTORIC_METRICS.exists() else None},
        "current_scoring_config": {"indicator_sets": {k: list(v) for k, v in DOMAIN_INDICATORS.items()}, "null_semantics": "explicit only where expected score is null, evidence_required=false, and case README documents evidence insufficiency; otherwise unlabelled_null"},
        "current_evaluation_code_fingerprint": source_fingerprint(current_sources),
        "historical_code_commit": None, "historical_prompt_snapshot": None,
        "limitations": ["当前源码指纹不能证明历史运行时的代码/prompt 哈希。", "没有历史 prompt 哈希；原始 JSON 中的模型观测记录不等同于完整请求配置快照。"],
    }
    _write_json(output_root / "recomputed_metrics_v2.json", metrics)
    _write_json(output_root / "legacy_metrics_v1_1_copy.json", old)
    _write_json(output_root / "per_case_indicator_run.json", details)
    _write_csv(output_root / "per_case_indicator_run.csv", details)
    _write_json(output_root / "citation_audit_by_run.json", citation_by_run)
    _write_json(output_root / "recompute_manifest.json", manifest)
    return {"output_root": str(output_root), "metrics": metrics, "manifest": manifest}


def _metric_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row["gold_status"] == "scored"]
    paired = [row for row in scored if row["absolute_error"] is not None]
    return {"slots": len(rows), "gold_scored": len(scored), "numeric_predictions": len(paired), "conditional_mae": round(sum(row["absolute_error"] for row in paired) / len(paired), 4) if paired else None, "gold_empty_predictions": sum(row["predicted_score"] is None for row in scored), "explicit_insufficient_scored": sum(row["gold_status"] == "explicit_insufficient_evidence" and isinstance(row["predicted_score"], (int, float)) for row in rows)}


def _citation_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def total(path: tuple[str, ...]) -> tuple[int, int]:
        n = count = 0
        for record in records:
            value: Any = record
            for part in path:
                value = value.get(part, {}) if isinstance(value, dict) else {}
            n += int(value.get("n", 0))
            count += int(value.get("resolvable_in_domain_evidence", value.get("belong_to_task_domain_evidence", value.get("located", 0))))
        return n, count
    structured = total(("structured_reference_ids",))
    free = total(("free_text_ids",))
    bound = sum(record["free_text_ids"]["bound_to_declared_support_location"] for record in records)
    locator = total(("original_text_page_locator",))
    return {"structured_reference_ids_resolvable": {"valid": structured[1], "total": structured[0]}, "free_text_ids_task_membership": {"valid": free[1], "total": free[0]}, "free_text_ids_bound_to_declared_support": {"valid": bound, "total": free[0]}, "text_and_page_locator": {"located": locator[1], "total": locator[0]}, "semantic_support_rate": None, "semantic_support_note": "未进行人工语义蕴含核验，不报告语义正确率。"}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--output-root", type=Path, default=None, help="必须为新目录；默认创建 runtime/review/recompute/run-*")
    args = parser.parse_args()
    output_root = args.output_root if args.output_root is not None else new_run_directory(ROOT / "runtime" / "review" / "recompute")
    print(json.dumps(recompute(args.input, output_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
