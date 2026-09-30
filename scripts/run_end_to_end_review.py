"""Run a real, isolated end-to-end technology and finance review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import Settings, get_settings  # noqa: E402
from app.finance.registry import FinanceRegistry  # noqa: E402
from app.finance.processor import TechnologyFinanceProcessor  # noqa: E402
from app.knowledge.contracts import KnowledgeLayer  # noqa: E402
from app.knowledge.assertions import EvidenceAssertionProcessor  # noqa: E402
from app.knowledge.identity import company_for_name  # noqa: E402
from app.knowledge.semantic.contracts import TechnologySemanticProfile  # noqa: E402
from app.knowledge.semantic.processor import TechnologyKnowledgeProcessor  # noqa: E402
from app.knowledge.semantic.extractor import source_quality  # noqa: E402
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase  # noqa: E402
from app.llm import OpenAICompatibleStructuredModel  # noqa: E402
from app.report_v2 import EvidenceFirstReportAssembler  # noqa: E402
from app.research.orchestrator import IterativeResearchService  # noqa: E402
from app.research.planner import RetrievalPlanner  # noqa: E402
from app.research.tavily_provider import TavilySearchProvider  # noqa: E402


def missing_provider_categories(settings: Settings) -> list[str]:
    """Return provider categories only; never expose environment values."""
    missing = []
    if not (settings.llm_endpoint and settings.llm_model and settings.llm_api_key):
        missing.append("LLM")
    if settings.web_search_provider != "tavily" or not settings.tavily_api_key:
        missing.append("Tavily")
    return missing


def make_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def choose_run_dir(output_dir: str | Path | None, run_id: str) -> Path:
    if output_dir is not None:
        return Path(output_dir).expanduser().resolve()
    return (ROOT / "runtime" / "review" / "end_to_end" / run_id).resolve()


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def _scope_for(chunk: dict[str, Any], version: dict[str, Any] | None) -> str:
    return str(
        (version or {}).get("metadata", {}).get("content_scope")
        or chunk.get("metadata", {}).get("content_scope")
        or "unknown"
    )


def build_source_inventory(knowledge_base: SharedKnowledgeBase, company_id: str) -> dict[str, Any]:
    chunks = knowledge_base.repository.list_current_chunks(company_id, KnowledgeLayer.GENERAL.value)
    records: dict[str, dict[str, Any]] = {}
    scope_counts: Counter[str] = Counter()
    for chunk in chunks:
        source = knowledge_base.repository.get_source(chunk.source_id)
        version = knowledge_base.repository.get_source_version(chunk.source_version_id or "")
        chunk_data = _dump(chunk)
        version_data = _dump(version) if version is not None else None
        scope_counts[_scope_for(chunk_data, version_data)] += 1
        if source is None:
            continue
        record = records.setdefault(source.source_id, {
            "source_type": source.source_type.value,
            "title": source.title,
            "url": source.canonical_url or "",
            "publisher": source.publisher or source.metadata.get("publisher"),
            "source_metadata": dict(source.metadata),
            "content_scope": _scope_for(chunk_data, version_data),
            "company_ids": set(),
            "chunk_count": 0,
            "_quality": source_quality(source, chunk, version).model_dump(mode="json") if version is not None else {"category": "unknown", "source_type": source.source_type.value},
        })
        record["company_ids"].add(chunk.company_id)
        record["chunk_count"] += 1
    sources = []
    for item in records.values():
        item["company_ids"] = sorted(value for value in item["company_ids"] if value)
        sources.append(item)
    sources.sort(key=lambda item: (item["source_type"], item["url"], item["title"]))
    source_type_counts = Counter(item["source_type"] for item in sources)
    quality_counts = Counter((item.get("_quality") or {}).get("category", "unknown") for item in sources)
    for item in sources:
        item["source_quality"] = item.pop("_quality")
    authoritative_first_party = quality_counts.get("authoritative_public_record", 0) + quality_counts.get("first_party", 0)
    return {
        "sources": sources,
        "source_count": len(sources),
        "source_type_distribution": dict(sorted(source_type_counts.items())),
        "source_quality_distribution": dict(sorted(quality_counts.items())),
        "authoritative_or_first_party_ratio": round(authoritative_first_party / len(sources), 4) if sources else 0.0,
        "unknown_web_ratio": round(source_type_counts.get("web", 0) / len(sources), 4) if sources else 0.0,
        "content_scope_chunk_counts": dict(sorted(scope_counts.items())),
    }


def _observation_bundles(finance: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *finance.get("funding_activities", []),
        *finance.get("risk_observations", []),
        *finance.get("monitoring_nodes", []),
    ]


def run_quality_checks(
    technology: dict[str, Any] | None,
    finance: dict[str, Any] | None,
    source_inventory: dict[str, Any],
    research: dict[str, Any],
    company_id: str | None,
    entity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply deterministic structural and provenance checks to processor output."""
    technology = technology or {}
    finance = finance or {}
    tech_facts = technology.get("technology_facts", [])
    fin_facts = finance.get("financial_facts", [])
    tech_ids = {item.get("fact_id") for item in tech_facts if item.get("fact_id")}
    fin_ids = {item.get("fact_id") for item in fin_facts if item.get("fact_id")}
    milestone_ids = {
        f"{item.get('template_id')}:{item.get('milestone_id')}"
        for item in technology.get("milestone_observations", [])
    }
    milestones_by_ref = {
        f"{item.get('template_id')}:{item.get('milestone_id')}": item
        for item in technology.get("milestone_observations", [])
    }
    finance_rules = FinanceRegistry().rule_by_id
    known_rules = set(finance.get("applicable_rule_ids", []))
    citation_missing_technology = [item.get("fact_id") for item in tech_facts if not item.get("citation")]
    citation_missing_finance = [item.get("fact_id") for item in fin_facts if not item.get("citation")]
    invalid_bundle_refs = []
    for index, observation in enumerate(_observation_bundles(finance)):
        bundle = observation.get("evidence_bundle") or {}
        invalid = {
            "technology_fact_ids": sorted(set(bundle.get("technology_fact_ids", [])) - tech_ids),
            "financial_fact_ids": sorted(set(bundle.get("financial_fact_ids", [])) - fin_ids),
            "milestone_refs": sorted(set(bundle.get("milestone_refs", [])) - milestone_ids),
            "rule_ids": sorted(set(bundle.get("rule_ids", [])) - known_rules),
        }
        if any(invalid.values()):
            invalid_bundle_refs.append({"observation_index": index, "invalid_references": invalid})

    quality_by_fact = {
        item.get("fact_id"): (item.get("source_quality") or {}).get("category", "unknown")
        for item in [*tech_facts, *fin_facts]
    }
    supported_with_non_supported_milestones = []
    conditioned_without_milestones = []
    milestone_missing_fact_ids = []
    supported_using_only_weak_evidence = []
    for index, observation in enumerate(_observation_bundles(finance)):
        bundle = observation.get("evidence_bundle") or {}
        refs = bundle.get("milestone_refs", [])
        rules = [finance_rules[rule_id] for rule_id in bundle.get("rule_ids", []) if rule_id in finance_rules]
        conditioned_rules = [rule for rule in rules if rule.applicable_conditions.get("milestone_any")]
        if conditioned_rules and not refs:
            conditioned_without_milestones.append({"observation_index": index, "rule_ids": [rule.rule_id for rule in conditioned_rules]})
        if observation.get("status") == "supported":
            unsupported_refs = [
                ref for ref in refs
                if ref not in milestones_by_ref or milestones_by_ref[ref].get("status") != "supported"
            ]
            if unsupported_refs:
                supported_with_non_supported_milestones.append({"observation_index": index, "milestone_refs": unsupported_refs})
        for ref in refs:
            milestone = milestones_by_ref.get(ref)
            if milestone is None:
                continue
            required_ids = set(milestone.get("supporting_fact_ids", [])) | set(milestone.get("contradicting_fact_ids", []))
            missing_ids = sorted(required_ids - set(bundle.get("technology_fact_ids", [])))
            if missing_ids:
                milestone_missing_fact_ids.append({"observation_index": index, "milestone_ref": ref, "missing_fact_ids": missing_ids})
        if observation.get("status") == "supported":
            evidence_ids = [*bundle.get("technology_fact_ids", []), *bundle.get("financial_fact_ids", [])]
            qualities = [quality_by_fact.get(fact_id, "unknown") for fact_id in evidence_ids]
            if qualities and all(category in {"snippet_only", "weak_web"} for category in qualities):
                supported_using_only_weak_evidence.append({"observation_index": index, "fact_ids": evidence_ids})

    tech_quality = Counter((item.get("source_quality") or {}).get("category", "unknown") for item in tech_facts)
    finance_quality = Counter((item.get("source_quality") or {}).get("category", "unknown") for item in fin_facts)
    snippet_supported = []
    for index, observation in enumerate(_observation_bundles(finance)):
        if observation.get("status") != "supported":
            continue
        bundle = observation.get("evidence_bundle") or {}
        used_ids = [*bundle.get("technology_fact_ids", []), *bundle.get("financial_fact_ids", [])]
        qualities = [quality_by_fact.get(item, "unknown") for item in used_ids]
        if qualities and all(item == "snippet_only" for item in qualities):
            snippet_supported.append({"kind": "finance_observation", "index": index, "fact_ids": used_ids})
    for index, observation in enumerate(technology.get("milestone_observations", [])):
        if observation.get("status") != "supported":
            continue
        fact_ids = observation.get("supporting_fact_ids", [])
        qualities = [quality_by_fact.get(item, "unknown") for item in fact_ids]
        if qualities and all(item == "snippet_only" for item in qualities):
            snippet_supported.append({
                "kind": "technology_milestone",
                "index": index,
                "milestone_ref": f"{observation.get('template_id')}:{observation.get('milestone_id')}",
                "fact_ids": fact_ids,
            })

    sources = source_inventory.get("sources", [])
    source_types = Counter(item.get("source_type", "unknown") for item in sources)
    total_sources = len(sources)
    unknown_web_ratio = source_types.get("web", 0) / total_sources if total_sources else 0.0
    host_counts = Counter((urlsplit(item.get("url", "")).hostname or "").lower() for item in sources)
    host_counts.pop("", None)
    top_host, top_host_count = host_counts.most_common(1)[0] if host_counts else (None, 0)
    top_host_ratio = top_host_count / total_sources if total_sources else 0.0
    entity = entity or {}
    resolved_without_website = entity.get("resolution_status") == "resolved" and not entity.get("official_website")

    unsupported_milestones = [
        f"{item.get('template_id')}:{item.get('milestone_id')}"
        for item in technology.get("milestone_observations", [])
        if item.get("status") == "supported" and not item.get("supporting_fact_ids")
    ]
    forbidden_fields = {"approve", "reject", "credit_limit", "risk_score", "loan_recommendation"}
    found_forbidden = _find_keys(finance, forbidden_fields)
    expected_company_id = company_id or ""
    inconsistent_sources = [
        {"url": item.get("url"), "company_ids": item.get("company_ids", [])}
        for item in sources
        if item.get("company_ids") != [expected_company_id]
    ]
    unversioned_source_metadata = [
        item.get("url") for item in sources
        if not (item.get("source_metadata") or {}).get("source_type_registry_version")
    ]
    gate_total = sum(
        int(research.get(key, 0))
        for key in ("relevant_source_count", "irrelevant_source_count", "uncertain_source_count")
    )
    gate_trace_consistent = gate_total >= int(research.get("sources_ingested", 0))

    checks = {
        "provenance": {
            "technology_facts_without_citation": citation_missing_technology,
            "financial_facts_without_citation": citation_missing_finance,
            "observations_with_unknown_evidence_ids": invalid_bundle_refs,
            "passed": not (citation_missing_technology or citation_missing_finance or invalid_bundle_refs),
        },
        "snippet_overuse": {
            "technology_snippet_only_fact_count": tech_quality.get("snippet_only", 0),
            "financial_snippet_only_fact_count": finance_quality.get("snippet_only", 0),
            "supported_observations_using_only_snippet_facts": snippet_supported,
            "passed": not snippet_supported,
        },
        "finance_evidence_strength": {
            "supported_outputs_with_non_supported_milestones": supported_with_non_supported_milestones,
            "milestone_conditioned_outputs_without_milestone_refs": conditioned_without_milestones,
            "milestone_evidence_missing_fact_ids": milestone_missing_fact_ids,
            "supported_outputs_using_only_weak_evidence": supported_using_only_weak_evidence,
            "passed": not (
                supported_with_non_supported_milestones
                or conditioned_without_milestones
                or milestone_missing_fact_ids
                or supported_using_only_weak_evidence
            ),
        },
        "source_mixture": {
            "source_type_distribution": dict(sorted(source_types.items())),
            "source_quality_distribution": source_inventory.get("source_quality_distribution", {}),
            "unknown_web_ratio": round(unknown_web_ratio, 4),
            "authoritative_or_first_party_ratio": source_inventory.get("authoritative_or_first_party_ratio", 0.0),
            "unknown_web_warning_threshold": 0.5,
            "most_repeated_host": top_host,
            "most_repeated_host_count": top_host_count,
            "most_repeated_host_ratio": round(top_host_ratio, 4),
            "repeated_host_warning_rule": "at least 3 sources and at least 50% of sampled source inventory",
        },
        "over_inference": {
            "supported_milestones_without_supporting_facts": unsupported_milestones,
            "passed": not unsupported_milestones,
        },
        "finance_boundary": {"prohibited_fields_found": found_forbidden, "passed": not found_forbidden},
        "entity_consistency": {
            "company_id": expected_company_id or None,
            "sources_with_mismatched_company_ids": inconsistent_sources,
            "sources_without_type_registry_version": unversioned_source_metadata,
            "knowledge_base_source_count": total_sources,
            "research_sources_ingested": int(research.get("sources_ingested", 0)),
            "aggregate_gate_counts_cover_ingested_sources": gate_trace_consistent,
            "per_source_gate_decision_persisted": False,
            "passed": not inconsistent_sources and not unversioned_source_metadata and total_sources <= int(research.get("sources_ingested", 0)) and gate_trace_consistent,
        },
        "official_website": {
            "resolved_entity_without_official_website": resolved_without_website,
            "passed": not resolved_without_website,
        },
    }

    warnings = []
    for key, label in (
        ("provenance", "来源溯源校验发现缺失或无效引用"),
        ("over_inference", "存在没有 supporting facts 的 supported milestone"),
        ("finance_boundary", "财务输出出现禁止字段"),
        ("finance_evidence_strength", "科技金融输出证据强度或里程碑来源链不完整"),
        ("entity_consistency", "来源 company_id 与企业或聚合 Gate 计数不一致"),
    ):
        if not checks[key]["passed"]:
            warnings.append(label)
    if resolved_without_website:
        warnings.append("主体已解析，但没有可验证的官方官网")
    if snippet_supported:
        warnings.append("存在仅由 snippet_only 事实支撑的 supported 财务观察")
    if total_sources and unknown_web_ratio > 0.5:
        warnings.append("unknown web 来源占比超过 50%")
    if top_host_count >= 3 and top_host_ratio >= 0.5:
        warnings.append(f"同一 host 来源重复较多：{top_host} ({top_host_count}/{total_sources})")
    if sources and not gate_trace_consistent:
        warnings.append("逐来源 relevance 判定未持久化；当前仅能用 company_id 与聚合 trace 核对准入路径")
    return {
        "checks": checks,
        "technology_source_quality_distribution": dict(sorted(tech_quality.items())),
        "financial_source_quality_distribution": dict(sorted(finance_quality.items())),
        "warnings": warnings,
        "design_findings": ([
            "逐来源 relevance 判定当前未持久化，无法从 review 工件独立回放某个 URL 的 Gate 决策；本轮只按现有 trace、company_id 和来源 metadata 核对。"
        ] if sources else []),
    }


def _find_keys(value: Any, targets: set[str], path: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in targets:
                found.append(key_path)
            found.extend(_find_keys(item, targets, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_keys(item, targets, f"{path}[{index}]"))
    return found


def _source_samples(inventory: dict[str, Any], limit: int = 15) -> list[dict[str, Any]]:
    return [
        {key: item.get(key) for key in ("source_type", "title", "url", "content_scope", "publisher", "source_quality")}
        for item in inventory.get("sources", [])[:limit]
    ]


def _source_url_by_chunk(knowledge_base: SharedKnowledgeBase, company_id: str) -> dict[str, str]:
    output = {}
    for chunk in knowledge_base.repository.list_current_chunks(company_id, KnowledgeLayer.GENERAL.value):
        output[chunk.chunk_id] = chunk.citation.source_url or ""
    return output


def _technology_summary(profile: TechnologySemanticProfile, source_urls: dict[str, str]) -> dict[str, Any]:
    data = profile.model_dump(mode="json")
    facts = data.get("technology_facts", [])
    milestone_counts = Counter(item.get("status", "unknown") for item in data.get("milestone_observations", []))
    return {
        "profile": data,
        "classifier_report": data.get("classifier_report", {}),
        "primary_domains": data["domain_profile"].get("primary_domains", []),
        "secondary_domains": data["domain_profile"].get("secondary_domains", []),
        "domain_evidence": [
            {"chunk_id": chunk_id, "source_url": source_urls.get(chunk_id, "")}
            for chunk_id in data["domain_profile"].get("evidence_chunk_ids", [])
        ],
        "selected_template_ids": data["template_selection"].get("selected_template_ids", []),
        "template_evidence": data["template_selection"].get("evidence", []),
        "technology_fact_type_registry_version": data.get("technology_fact_type_registry_version", "unknown"),
        "rejected_fact_type_count": data.get("fact_extraction_report", {}).get("rejected_fact_type_count", 0),
        "rejected_fact_type_distribution": data.get("fact_extraction_report", {}).get("rejected_fact_type_distribution", {}),
        "technology_fact_count": len(facts),
        "fact_type_distribution": dict(sorted(Counter(item.get("fact_type", "unknown") for item in facts).items())),
        "source_quality_distribution": dict(sorted(Counter((item.get("source_quality") or {}).get("category", "unknown") for item in facts).items())),
        "technology_fact_sample": [
            {key: item.get(key) for key in ("subject", "predicate", "object_value", "fact_type")}
            | {"source_quality": (item.get("source_quality") or {}).get("category"), "source_url": (item.get("citation") or {}).get("source_url")}
            for item in facts[:20]
        ],
        "milestone_observations": [
            item for item in data.get("milestone_observations", []) if item.get("status") != "no_evidence"
        ],
        "milestone_status_counts": dict(sorted(milestone_counts.items())),
        "blocked_inference_count": sum(len(item.get("blocked_inferences", [])) for item in data.get("milestone_observations", [])),
    }


def _finance_summary(profile: Any, execution_trace: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _dump(profile)
    facts = data.get("financial_facts", [])
    extraction_report = data.get("financial_fact_extraction_report", {})
    trace = execution_trace or {}
    mapping_audit = {
        "mapping_mode": trace.get("mapping_mode", "deterministic_registry"),
        "mapping_status": trace.get("finance_mapping_status", "completed"),
        "candidate_rule_count": trace.get("finance_candidate_rule_count", 0),
        "candidate_rule_ids": trace.get("finance_candidate_rules", []),
        "selected_rule_count": len(trace.get("finance_selected_rules", trace.get("finance_applied_rules", data.get("applicable_rule_ids", [])))),
        "selected_rule_ids": trace.get("finance_selected_rules", trace.get("finance_applied_rules", data.get("applicable_rule_ids", []))),
        "applied_rule_ids": trace.get("finance_applied_rules", data.get("applicable_rule_ids", [])),
        "milestone_conditioned_candidate_count": trace.get("finance_milestone_conditioned_candidate_count", 0),
        "general_candidate_count": trace.get("finance_general_candidate_count", 0),
        "llm_mapping_call": "none",
    }
    return {
        "profile": data,
        "financial_fact_count": len(facts),
        "financial_dimension_distribution": dict(sorted(Counter(item.get("financial_dimension", "unknown") for item in facts).items())),
        "financial_fact_extraction_report": extraction_report,
        "financial_source_quality_distribution": extraction_report.get("source_quality_distribution", {}),
        "financial_fact_sample": [
            {
                "dimension": item.get("financial_dimension"),
                "subject": item.get("subject"),
                "predicate": item.get("predicate"),
                "object": item.get("object_value"),
                "period": item.get("period"),
                "value": item.get("quantitative_value"),
                "unit": item.get("quantitative_unit"),
                "currency": item.get("currency"),
                "source_quality": (item.get("source_quality") or {}).get("category"),
                "source_url": (item.get("citation") or {}).get("source_url"),
            }
            for item in facts[:20]
        ],
        "financial_scenarios": data.get("financial_scenarios", []),
        "funding_activities": data.get("funding_activities", []),
        "risk_observations": data.get("risk_observations", []),
        "monitoring_nodes": data.get("monitoring_nodes", []),
        "financial_information_gaps": data.get("financial_information_gaps", []),
        "applicable_rule_ids": data.get("applicable_rule_ids", []),
        "mapping_audit": mapping_audit,
    }


def _entity_review(input_name: str, research_result: dict[str, Any], company: Any | None) -> dict[str, Any]:
    trace = research_result.get("identity_trace") or {}
    metadata = getattr(company, "metadata", {}) or {}
    candidate_evidence = [
        url
        for candidate in research_result.get("identity_candidates", [])
        for url in candidate.get("evidence_urls", [])
    ]
    evidence_urls = metadata.get("resolution_evidence_urls") or candidate_evidence
    return {
        "input_name": input_name,
        "canonical_name": research_result.get("resolved_canonical_name") or getattr(company, "canonical_name", None),
        "aliases": list(getattr(company, "aliases", []) or []),
        "official_website": research_result.get("official_website") or getattr(company, "official_website", None),
        "official_website_initial": research_result.get("official_website_initial"),
        "official_website_final": research_result.get("official_website") or getattr(company, "official_website", None),
        "official_website_enriched_during_research": research_result.get("official_website_enriched_during_research", False),
        "official_website_enrichment_round": research_result.get("official_website_enrichment_round"),
        "official_website_enrichment_diagnostic": research_result.get("official_website_enrichment_diagnostic"),
        "official_website_strong_candidate_hosts": research_result.get("official_website_strong_candidate_hosts", trace.get("official_website_strong_candidate_hosts", [])),
        "official_website_external_candidate_hosts": research_result.get("official_website_external_candidate_hosts", trace.get("official_website_external_candidate_hosts", [])),
        "official_website_resolution_source": metadata.get("official_website_resolution_source"),
        "official_website_evidence_url": metadata.get("official_website_evidence_url"),
        "official_website_diagnostic": metadata.get("official_website_diagnostic"),
        "company_id": getattr(company, "company_id", None),
        "resolution_status": research_result.get("entity_resolution_status", "unresolved"),
        "identity_evidence": {"count": research_result.get("identity_evidence_count", 0), "urls": evidence_urls, "trace": trace},
    }


def render_review_markdown(review: dict[str, Any]) -> str:
    entity = review.get("entity", {})
    research = review.get("research", {})
    semantic = review.get("technology_semantic") or {}
    finance = review.get("technology_finance") or {}
    assertions = review.get("evidence_assertions") or {}
    evidence_report = review.get("evidence_first_report") or {}
    quality = review.get("quality_checks", {})
    lines = [
        "# KeHeng End-to-End Review", "",
        "## 1. Run", "",
        f"- Run ID: `{review.get('run', {}).get('run_id', '')}`",
        f"- Status: `{review.get('run', {}).get('status', '')}`",
        f"- Started: `{review.get('run', {}).get('started_at', '')}`",
        f"- Model: `{review.get('run', {}).get('llm_model', '')}`",
        f"- Search provider: `{review.get('run', {}).get('search_provider', '')}`", "",
        "## 2. Entity Resolution", "",
        f"- Input: {entity.get('input_name')}",
        f"- Status: `{entity.get('resolution_status')}`",
        f"- Canonical name: {entity.get('canonical_name')}",
        f"- Aliases: {', '.join(entity.get('aliases', [])) or '无'}",
        f"- Official website: {entity.get('official_website') or '无'}",
        f"- Official website initial / final: {entity.get('official_website_initial') or '无'} / {entity.get('official_website_final') or '无'}",
        f"- Enriched during research / round: {entity.get('official_website_enriched_during_research', False)} / {entity.get('official_website_enrichment_round') or '无'}",
        f"- Official website resolution: `{entity.get('official_website_resolution_source') or 'none'}`; evidence: {entity.get('official_website_evidence_url') or '无'}; diagnostic: `{entity.get('official_website_diagnostic') or 'none'}`",
        f"- Official website discovery: verified `{entity.get('official_website') or '无'}`; strong candidates: `{json.dumps(entity.get('official_website_strong_candidate_hosts', []), ensure_ascii=False)}`; external/unverified candidates: `{json.dumps(entity.get('official_website_external_candidate_hosts', []), ensure_ascii=False)}`; diagnostic: `{entity.get('official_website_enrichment_diagnostic') or entity.get('official_website_diagnostic') or 'none'}`",
        f"- Identity evidence URLs: {', '.join(entity.get('identity_evidence', {}).get('urls', [])) or '无'}", "",
        "## 3. Iterative Research", "",
        f"- Rounds: {research.get('rounds', 0)}",
        f"- Current stage: `{research.get('current_stage', '')}`",
        f"- Identity search results: {research.get('identity_results_count', 0)}",
        f"- Stop reason: `{research.get('stop_reason', '')}`",
        f"- Queries: {len(research.get('queries_executed', []))}",
        f"- Sources found / ingested: {research.get('sources_found', 0)} / {research.get('sources_ingested', 0)}",
        f"- Relevant / irrelevant / uncertain: {research.get('relevant_source_count', 0)} / {research.get('irrelevant_source_count', 0)} / {research.get('uncertain_source_count', 0)}",
        f"- New / duplicate versions: {research.get('new_versions', 0)} / {research.get('duplicate_versions', 0)}",
        f"- Knowledge chunks added: {research.get('knowledge_chunks_added', 0)}", "",
        f"- Official-host fast paths: {research.get('official_host_fast_path_count', 0)}",
        f"- Official website enriched during research: {research.get('official_website_enriched_during_research', False)} (round {research.get('official_website_enrichment_round') or '无'}; diagnostic `{research.get('official_website_enrichment_diagnostic') or 'none'}`)",
        f"- Official website initial / final: {research.get('official_website_initial') or '无'} / {research.get('official_website') or '无'}", "",
        "### Queries and round trace", "",
    ]
    lines.extend(f"- {query}" for query in research.get("queries_executed", []))
    for entry in research.get("trace", []):
        lines.extend([
            "", f"### Round {entry.get('round')}", "",
            f"- Queries: {', '.join(entry.get('queries', [])) or '无'}",
            f"- Results / new / duplicate: {entry.get('results_count', 0)} / {entry.get('new_source_count', 0)} / {entry.get('duplicate_source_count', 0)}",
            f"- Relevant / irrelevant / uncertain: {entry.get('relevant_source_count', 0)} / {entry.get('irrelevant_source_count', 0)} / {entry.get('uncertain_source_count', 0)}",
            f"- Source types: `{json.dumps(entry.get('source_type_counts', {}), ensure_ascii=False)}`",
            f"- Relevance diagnostics: `{json.dumps(entry.get('relevance_diagnostics', {}), ensure_ascii=False)}`",
            f"- New terms: {', '.join(entry.get('new_terms', [])) or '无'}",
            f"- Information gaps: {', '.join(entry.get('information_gaps', [])) or '无'}",
        ])
    semantic_execution = review.get("semantic_execution") or {}
    if semantic_execution:
        classifier_selection = semantic.get("profile", {}).get("classifier_evidence_selection") or semantic.get("profile", {}).get("semantic_evidence_selection") or semantic_execution.get("classifier_evidence_selection") or {
            "available_chunk_count": semantic_execution.get("available_chunk_count", 0),
            "selected_chunk_count": semantic_execution.get("selected_chunk_count", 0),
            "selected_source_count": semantic_execution.get("selected_source_count", 0),
            "selected_char_count": semantic_execution.get("selected_char_count", 0),
            "truncated_chunk_count": semantic_execution.get("truncated_chunk_count", 0),
            "quality_distribution": semantic_execution.get("quality_distribution", {}),
            "content_scope_distribution": semantic_execution.get("content_scope_distribution", {}),
            "source_type_distribution": semantic_execution.get("source_type_distribution", {}),
            "dropped_due_to_budget": semantic_execution.get("dropped_due_to_budget", 0),
            "dropped_due_to_source_cap": semantic_execution.get("dropped_due_to_source_cap", 0),
            "dropped_as_duplicate": semantic_execution.get("dropped_as_duplicate", 0),
        }
        fact_selection = semantic.get("profile", {}).get("fact_evidence_selection") or semantic_execution.get("fact_evidence_selection", {})
        lines.extend([
            "", "### Semantic evidence selection and fact extraction", "",
            f"- Semantic stage / substage: `{semantic_execution.get('semantic_stage', '')}` / `{semantic_execution.get('semantic_substage', '')}`",
            f"- Classifier evidence: {classifier_selection.get('available_chunk_count', 0)} → {classifier_selection.get('selected_chunk_count', 0)} chunks; {classifier_selection.get('selected_source_count', 0)} sources; {classifier_selection.get('selected_char_count', 0)} chars",
            f"- Classifier quality / content scope / source types: `{json.dumps(classifier_selection.get('quality_distribution', {}), ensure_ascii=False)}` / `{json.dumps(classifier_selection.get('content_scope_distribution', {}), ensure_ascii=False)}` / `{json.dumps(classifier_selection.get('source_type_distribution', {}), ensure_ascii=False)}`",
            f"- Classifier truncated chunks: {classifier_selection.get('truncated_chunk_count', 0)}",
            f"- Classifier status / evidence: `{semantic_execution.get('classifier_status', 'unknown')}` / {semantic_execution.get('classifier_input_evidence_count', 0)}",
            f"- Classifier invalid refs / dropped templates / downgraded: `{json.dumps(semantic_execution.get('classifier_invalid_evidence_refs', []), ensure_ascii=False)}` / {semantic_execution.get('classifier_dropped_templates', 0)} / {semantic_execution.get('classifier_downgraded', False)}",
            f"- Classifier report: `{json.dumps(semantic_execution.get('classifier_report', {}), ensure_ascii=False)}`",
            f"- Classifier dropped (budget / source cap / duplicate): {classifier_selection.get('dropped_due_to_budget', 0)} / {classifier_selection.get('dropped_due_to_source_cap', 0)} / {classifier_selection.get('dropped_as_duplicate', 0)}",
            f"- Technology fact evidence: {fact_selection.get('available_chunk_count', 0)} → {fact_selection.get('selected_chunk_count', 0)} chunks; positive matches {fact_selection.get('positive_match_candidate_count', 0)} candidates / {fact_selection.get('positive_match_selected_count', 0)} selected; fallback {fact_selection.get('fallback_fill_count', 0)}",
            f"- Fact evidence sources / chars / quality: {fact_selection.get('selected_source_count', 0)} / {fact_selection.get('selected_char_count', 0)} / `{json.dumps(fact_selection.get('quality_distribution', {}), ensure_ascii=False)}`",
            f"- Fact evidence templates / score distribution: `{json.dumps(fact_selection.get('selected_template_ids', []), ensure_ascii=False)}` / `{json.dumps(fact_selection.get('positive_match_score_distribution', {}), ensure_ascii=False)}`",
            f"- Fact extraction batches: {semantic_execution.get('fact_extraction_batch_count', 0)}",
            f"- Succeeded / failed batches: {semantic_execution.get('fact_extraction_completed_batches', 0)} / {semantic_execution.get('fact_extraction_failed_batches', 0)}",
            f"- Input chunks covered (successful / total; failed): {semantic_execution.get('fact_extraction_successful_chunks', 0)} / {semantic_execution.get('fact_extraction_input_chunks', 0)}; {semantic_execution.get('fact_extraction_failed_chunks', 0)}",
            f"- Extracted facts: {semantic_execution.get('fact_extraction_fact_count', 0)}",
            f"- Technology fact type registry version: `{semantic_execution.get('technology_fact_type_registry_version', 'unknown')}`",
            f"- Rejected technology fact types: {semantic_execution.get('fact_extraction_rejected_fact_type_count', 0)}; distribution `{json.dumps(semantic_execution.get('fact_extraction_rejected_fact_type_distribution', {}), ensure_ascii=False)}`",
            f"- Fact extraction batch errors: `{json.dumps(semantic_execution.get('fact_extraction_error_categories', {}), ensure_ascii=False)}`",
        ])
    lines.extend(["", "## 4. Source Quality", "", "| SourceType | Quality | Title | URL | content_scope | Publisher |", "|---|---|---|---|---|---|"])
    for item in research.get("source_samples", []):
        row = {key: str(item.get(key) or "").replace("|", "\\|") for key in ("source_type", "title", "url", "content_scope", "publisher")}
        row["quality"] = str((item.get("source_quality") or {}).get("category", "unknown"))
        lines.append("| {source_type} | {quality} | {title} | {url} | {content_scope} | {publisher} |".format(**row))
    lines.extend([
        "", f"- Content scope chunk counts: `{json.dumps(research.get('content_scope_chunk_counts', {}), ensure_ascii=False)}`",
        f"- Source type distribution: `{json.dumps(research.get('source_type_distribution', {}), ensure_ascii=False)}`", "",
        f"- Source quality distribution: `{json.dumps(research.get('source_quality_distribution', {}), ensure_ascii=False)}`",
        f"- Authoritative / first-party ratio: `{research.get('authoritative_or_first_party_ratio', 0.0)}`",
        f"- Unknown web ratio: `{research.get('unknown_web_ratio', 0.0)}`", "",
        "## 5. Technology Understanding", "",
        f"- Primary domains: {', '.join(semantic.get('primary_domains', [])) or '无'}",
        f"- Secondary domains: {', '.join(semantic.get('secondary_domains', [])) or '无'}",
        f"- Domain evidence: `{json.dumps(semantic.get('domain_evidence', []), ensure_ascii=False)}`",
        f"- Selected templates: {', '.join(semantic.get('selected_template_ids', [])) or '无'}",
        f"- Template evidence: `{json.dumps(semantic.get('template_evidence', []), ensure_ascii=False)}`",
        f"- Classifier report: `{json.dumps(semantic.get('classifier_report', {}), ensure_ascii=False)}`",
        f"- Technology facts: {semantic.get('technology_fact_count', 0)}",
        f"- Technology fact type registry version: `{semantic.get('technology_fact_type_registry_version', 'unknown')}`",
        f"- Rejected technology fact types: {semantic.get('rejected_fact_type_count', 0)}; distribution `{json.dumps(semantic.get('rejected_fact_type_distribution', {}), ensure_ascii=False)}`",
        f"- Fact types: `{json.dumps(semantic.get('fact_type_distribution', {}), ensure_ascii=False)}`",
        f"- Source quality: `{json.dumps(semantic.get('source_quality_distribution', {}), ensure_ascii=False)}`", "",
        "### Technology fact sample", "",
    ])
    for fact in semantic.get("technology_fact_sample", []):
        lines.append(f"- {fact.get('subject')} — {fact.get('predicate')} — {fact.get('object_value')} ({fact.get('fact_type')}; {fact.get('source_quality')}; {fact.get('source_url')})")
    lines.extend(["", "## 6. Technology Milestones", ""])
    lines.append(f"- Status counts: `{json.dumps(semantic.get('milestone_status_counts', {}), ensure_ascii=False)}`")
    lines.append(f"- Blocked inference count: {semantic.get('blocked_inference_count', 0)}")
    for item in semantic.get("milestone_observations", []):
        lines.append(f"- `{item.get('template_id')}:{item.get('milestone_id')}` — {item.get('status')}; supporting={item.get('supporting_fact_ids', [])}; contradicting={item.get('contradicting_fact_ids', [])}; reason={item.get('reason')}; blocked={item.get('blocked_inferences', [])}")
    extraction_report = finance.get("financial_fact_extraction_report", {})
    lines.extend([
        "", "## 7. Financial Facts",
        "", f"- Count: {finance.get('financial_fact_count', 0)}",
        f"- Dimensions: `{json.dumps(finance.get('financial_dimension_distribution', {}), ensure_ascii=False)}`",
        f"- Extraction batches: {extraction_report.get('successful_batch_count', 0)}/{extraction_report.get('batch_count', 0)} succeeded; {extraction_report.get('failed_batch_count', 0)} failed",
        f"- Extraction chunks: {extraction_report.get('successful_chunk_count', 0)}/{extraction_report.get('input_chunk_count', 0)} succeeded; {extraction_report.get('failed_chunk_count', 0)} failed",
        f"- Extraction source quality: `{json.dumps(extraction_report.get('source_quality_distribution', {}), ensure_ascii=False)}`",
        f"- Extraction errors: `{json.dumps(extraction_report.get('error_categories', {}), ensure_ascii=False)}`",
        f"- Rejected references / dimensions: {extraction_report.get('rejected_evidence_reference_count', 0)} / {extraction_report.get('rejected_dimension_count', 0)}",
    ])
    for fact in finance.get("financial_fact_sample", []):
        lines.append(f"- {fact.get('dimension')}: {fact.get('subject')} — {fact.get('predicate')} — {fact.get('object')} ({fact.get('period') or 'period 未提供'}; {fact.get('value')} {fact.get('unit') or ''} {fact.get('currency') or ''}; {fact.get('source_quality')}; {fact.get('source_url')})")
    audit = finance.get("mapping_audit", {})
    lines.extend(["", "## 8. Technology-Finance Mapping", "", f"- Mapping mode/status: `{audit.get('mapping_mode', 'unknown')}` / `{audit.get('mapping_status', 'unknown')}`", f"- Candidate rules ({audit.get('candidate_rule_count', 0)}): `{json.dumps(audit.get('candidate_rule_ids', []), ensure_ascii=False)}`", f"- Selected rules ({audit.get('selected_rule_count', 0)}): `{json.dumps(audit.get('selected_rule_ids', []), ensure_ascii=False)}`", f"- Applied rules ({len(audit.get('applied_rule_ids', finance.get('applicable_rule_ids', [])))}): `{json.dumps(audit.get('applied_rule_ids', finance.get('applicable_rule_ids', [])), ensure_ascii=False)}`", f"- Milestone-conditioned / general candidates: {audit.get('milestone_conditioned_candidate_count', 0)} / {audit.get('general_candidate_count', 0)}", f"- LLM mapping call: `{audit.get('llm_mapping_call', 'unknown')}`", f"- Financial scenarios: `{json.dumps(finance.get('financial_scenarios', []), ensure_ascii=False)}`", f"- Applicable rules: `{json.dumps(finance.get('applicable_rule_ids', []), ensure_ascii=False)}`"])
    for label, key in (("Funding activities", "funding_activities"), ("Risk observations", "risk_observations")):
        lines.extend(["", f"### {label}"])
        for item in finance.get(key, []):
            lines.append(f"- status={item.get('status')}; scenario={item.get('scenario_id')}; activities={item.get('funding_activities') or item.get('risk_theme')}; reason={item.get('reason')}; evidence_bundle=`{json.dumps(item.get('evidence_bundle', {}), ensure_ascii=False)}`")
    lines.extend(["", "## 9. Monitoring Nodes", ""])
    for item in finance.get("monitoring_nodes", []):
        lines.append(f"- {item.get('name')} — {item.get('status')}; reason={item.get('reason')}; required evidence={item.get('required_evidence_types', [])}; triggering milestones={item.get('triggering_milestone_ids', [])}; rule={item.get('evidence_bundle', {}).get('rule_ids', [])}; evidence_bundle=`{json.dumps(item.get('evidence_bundle', {}), ensure_ascii=False)}`")
    lines.extend(["", "## 10. Information Gaps", ""])
    for item in finance.get("financial_information_gaps", []):
        lines.append(f"- {item.get('dimension_id')}: {item.get('description')} (requested={item.get('requested_fields', [])}; rules={item.get('source_rule_ids', [])})")
    for item in research.get("remaining_information_gaps", []):
        lines.append(f"- Research: {item}")
    lines.extend(["", "## 11. Quality Warnings", ""])
    lines.extend(f"- {item}" for item in quality.get("warnings", []))
    if not quality.get("warnings"):
        lines.append("- 无")
    lines.extend(["", "### Deterministic checks", "", f"```json\n{json.dumps(quality.get('checks', {}), ensure_ascii=False, indent=2)}\n```", "", "## 12. Overall Pipeline Statistics", ""])
    lines.append(f"- Technology facts: {semantic.get('technology_fact_count', 0)}")
    lines.append(f"- Financial facts: {finance.get('financial_fact_count', 0)}")
    lines.append(f"- Funding activities: {len(finance.get('funding_activities', []))}")
    lines.append(f"- Risk observations: {len(finance.get('risk_observations', []))}")
    lines.append(f"- Monitoring nodes: {len(finance.get('monitoring_nodes', []))}")
    lines.append(f"- Financial gaps: {len(finance.get('financial_information_gaps', []))}")
    if assertions:
        lines.extend(["", "## 13. Evidence Assertions", ""])
        lines.append(f"- Atomic facts / assertions: {assertions.get('atomic_fact_count', 0)} / {assertions.get('assertion_count', 0)}")
        lines.append(f"- Fact-to-assertion compression ratio: {assertions.get('fact_to_assertion_compression_ratio')}")
        lines.append(f"- Technology / financial assertions: {assertions.get('technology_assertion_count', 0)} / {assertions.get('financial_assertion_count', 0)}")
        lines.append(f"- Single-source / multi-source / conflict: {assertions.get('single_source_count', 0)} / {assertions.get('multi_source_support_count', 0)} / {assertions.get('conflict_count', 0)}")
        lines.append(f"- Distinct-source support distribution (1 / 2 / 3+): `{json.dumps(assertions.get('support_source_distribution', {}), ensure_ascii=False)}`")
        lines.append(f"- Strongest source quality distribution: `{json.dumps(assertions.get('strongest_source_quality_distribution', {}), ensure_ascii=False)}`")
    if evidence_report:
        report = evidence_report.get("summary", {})
        lines.extend([
            "", "## Evidence-first report", "",
            f"- Report JSON: `{evidence_report.get('report_json')}`",
            f"- Technology milestones: {report.get('milestone_count', 0)}",
            f"- Technology facts shown: {report.get('technology_facts_shown', 0)} / {report.get('technology_fact_count', 0)}",
            f"- Financial dimensions: {report.get('financial_dimension_count', 0)}",
            f"- Financial facts shown: {report.get('financial_facts_shown', 0)} / {report.get('financial_fact_count', 0)}",
            f"- Funding activities: {report.get('funding_activity_count', 0)}; risks: {report.get('risk_count', 0)}; monitoring nodes: {report.get('monitoring_node_count', 0)}",
            f"- Financial gaps: {report.get('financial_gap_count', 0)} → {report.get('deduplicated_financial_dimension_count', 0)} dimensions",
            f"- Semantic gaps shown: {report.get('semantic_gap_count', 0)}",
        ])
    lines.append(f"- Design findings: `{json.dumps(review.get('design_findings', []), ensure_ascii=False)}`")
    if review.get("finance_execution"):
        lines.extend([
            "", "## Finance Execution Trace", "",
            f"```json\n{json.dumps(review['finance_execution'], ensure_ascii=False, indent=2)}\n```",
        ])
    if review.get("errors"):
        lines.extend(["", "## Pipeline Errors", ""])
        for item in review["errors"]:
            semantic_substage = item.get("semantic_substage")
            finance_substage = item.get("finance_substage")
            substage = semantic_substage or finance_substage
            stage_label = f"{item.get('stage')} at {substage}" if substage else item.get("stage")
            lines.append(f"- Stage `{stage_label}`: `{item.get('category')}`")
            for diagnostic in item.get("diagnostics", []):
                attempt = diagnostic.get("attempt", "?")
                location = diagnostic.get("location", "response")
                kind = diagnostic.get("type", "error")
                message = diagnostic.get("message", "")
                lines.append(
                    f"  - Attempt {attempt}, `{location}` (`{kind}`): {message}"
                )
    return "\n".join(lines).rstrip() + "\n"


async def execute_review(args: argparse.Namespace, settings: Settings, run_dir: Path, run_id: str) -> dict[str, Any]:
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Review output directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir = run_dir / "knowledge"
    knowledge_base = SharedKnowledgeBase(knowledge_dir)
    started_at = datetime.now(timezone.utc).isoformat()
    run_meta = {
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "input_name": args.enterprise_name,
        "llm_model": settings.llm_model,
        "search_provider": settings.web_search_provider,
        "search_depth": settings.tavily_search_depth,
        "parameters": {
            "max_rounds": args.max_rounds,
            "max_queries_per_round": args.max_queries_per_round,
            "max_results_per_query": args.max_results_per_query,
        },
    }
    review: dict[str, Any] = {
        "run": run_meta,
        "entity": {
            "input_name": args.enterprise_name,
            "canonical_name": None,
            "aliases": [],
            "official_website": None,
            "resolution_status": "not_started",
            "identity_evidence": {"count": 0, "urls": [], "trace": {}},
        },
        "research": {},
        "technology_semantic": None,
        "technology_finance": None,
        "evidence_assertions": None,
        "quality_checks": {},
        "design_findings": [],
        "errors": [],
    }
    company = None
    research_service = None
    semantic_processor = None
    finance_processor = None
    inventory: dict[str, Any] = {"sources": [], "source_count": 0, "content_scope_chunk_counts": {}}
    stage = "research"
    try:
        model = OpenAICompatibleStructuredModel(settings.llm_endpoint, settings.llm_model, settings.llm_api_key, timeout=settings.llm_timeout_seconds, enable_thinking=settings.llm_enable_thinking)
        search = TavilySearchProvider(settings.tavily_api_key, search_depth=settings.tavily_search_depth, timeout=settings.web_search_timeout_seconds)
        research_service = IterativeResearchService(RetrievalPlanner(model), search, knowledge_base)
        result = await research_service.run(
            args.enterprise_name,
            max_rounds=args.max_rounds,
            max_queries_per_round=args.max_queries_per_round,
            max_results_per_query=args.max_results_per_query,
        )
        research_data = result.model_dump(mode="json")
        research_data["current_stage"] = research_service.last_execution_trace.get(
            "current_stage", "research_completed"
        )
        research_data["identity_results_count"] = research_service.last_execution_trace.get(
            "identity_results_count", 0
        )
        research_data["execution_trace"] = dict(research_service.last_execution_trace)
        research_data["official_host_fast_path_count"] = sum(
            int((entry.relevance_diagnostics or {}).get("official_host_fast_path_count", 0))
            for entry in result.trace
        )
        company_seed = company_for_name(args.enterprise_name)
        company = knowledge_base.repository.get_company(company_seed.company_id or "")
        inventory = build_source_inventory(knowledge_base, company_seed.company_id or "")
        research_data["source_samples"] = _source_samples(inventory)
        research_data["source_type_distribution"] = inventory["source_type_distribution"]
        research_data["source_quality_distribution"] = inventory["source_quality_distribution"]
        research_data["authoritative_or_first_party_ratio"] = inventory["authoritative_or_first_party_ratio"]
        research_data["unknown_web_ratio"] = inventory["unknown_web_ratio"]
        research_data["content_scope_chunk_counts"] = inventory["content_scope_chunk_counts"]
        review["entity"] = _entity_review(args.enterprise_name, research_data, company)
        review["research"] = research_data

        if result.entity_resolution_status == "resolved" and company is not None:
            stage = "technology_semantic"
            semantic_processor = TechnologyKnowledgeProcessor(model, knowledge_base)
            semantic_profile = await semantic_processor.process_company(company.company_id or "")
            review["semantic_execution"] = dict(semantic_processor.last_execution_trace)
            review["technology_semantic"] = _technology_summary(semantic_profile, _source_url_by_chunk(knowledge_base, company.company_id or ""))
            stage = "technology_finance"
            finance_processor = TechnologyFinanceProcessor(model, knowledge_base)
            finance_profile = await finance_processor.process_company(company.company_id or "")
            review["finance_execution"] = dict(finance_processor.last_execution_trace)
            review["technology_finance"] = _finance_summary(finance_profile, finance_processor.last_execution_trace)
            stage = "evidence_assertions"
            assertion_processor = EvidenceAssertionProcessor(knowledge_base)
            assertion_profile = assertion_processor.process_company(company.company_id or "")
            review["assertion_execution"] = dict(assertion_processor.last_execution_trace)
            source_distribution = Counter()
            strongest_distribution = Counter()
            member_quality_distribution = Counter()
            for assertion in assertion_profile.assertions:
                source_distribution["1" if assertion.supporting_source_count == 1 else "2" if assertion.supporting_source_count == 2 else "3+"] += 1
                strongest_distribution[assertion.strongest_source_quality] += 1
                for quality_name, count in assertion.source_quality_distribution.items():
                    member_quality_distribution[quality_name] += count
            atomic_count = len(semantic_profile.technology_facts) + len(finance_profile.financial_facts)
            assertion_count = len(assertion_profile.assertions)
            review["evidence_assertions"] = {
                "profile": _dump(assertion_profile),
                "atomic_technology_fact_count": len(semantic_profile.technology_facts),
                "atomic_financial_fact_count": len(finance_profile.financial_facts),
                "atomic_fact_count": atomic_count,
                "assertion_count": assertion_count,
                "technology_assertion_count": assertion_profile.technology_assertion_count,
                "financial_assertion_count": assertion_profile.financial_assertion_count,
                "single_source_count": assertion_profile.single_source_count,
                "multi_source_support_count": assertion_profile.multi_source_support_count,
                "conflict_count": assertion_profile.conflict_count,
                "fact_to_assertion_compression_ratio": round(atomic_count / assertion_count, 4) if assertion_count else None,
                "support_source_distribution": dict(sorted(source_distribution.items())),
                "strongest_source_quality_distribution": dict(sorted(strongest_distribution.items())),
                "member_source_quality_distribution": dict(sorted(member_quality_distribution.items())),
            }
            stage = "evidence_first_report"
            report = EvidenceFirstReportAssembler(knowledge_base.repository).build(company.company_id or "")
            report_dir = run_dir / "evidence_first_report"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / "report.json"
            report_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
            review["evidence_first_report"] = {
                "report_id": report.report_id,
                "report_json": str(report_path),
                "summary": {
                    "milestone_count": len(report.technology_milestones),
                    "technology_facts_shown": len(report.technology_profile.representative_facts),
                    "technology_fact_count": report.technology_profile.fact_count,
                    "financial_dimension_count": len(report.financial_profile.dimensions),
                    "financial_facts_shown": sum(len(item.representative_facts) for item in report.financial_profile.dimensions),
                    "financial_fact_count": report.financial_profile.fact_count,
                    "funding_activity_count": len(report.technology_finance_links.funding_activities),
                    "risk_count": len(report.technology_finance_links.risks),
                    "monitoring_node_count": len(report.technology_finance_links.monitoring_nodes),
                    "financial_gap_count": report.information_gaps.total_gap_count,
                    "deduplicated_financial_dimension_count": report.information_gaps.deduplicated_financial_dimension_count,
                    "semantic_gap_count": len(report.information_gaps.semantic_gaps),
                },
            }
            review["run"]["status"] = "completed"
        else:
            review["run"]["status"] = "entity_not_resolved"
        review["quality_checks"] = run_quality_checks(
            (review.get("technology_semantic") or {}).get("profile"),
            (review.get("technology_finance") or {}).get("profile"),
            inventory,
            research_data,
            getattr(company, "company_id", None),
            review.get("entity"),
        )
        review["design_findings"] = review["quality_checks"].get("design_findings", [])
    except Exception as exc:  # include stage and class only, so provider secrets/body text cannot leak
        if not review.get("research") and research_service is not None:
            snapshot = dict(research_service.last_execution_trace)
            if snapshot:
                review["research"] = {
                    "enterprise_name": snapshot.get("enterprise_name", args.enterprise_name),
                    "queries_executed": list(snapshot.get("queries_executed", [])),
                    "identity_queries": list(snapshot.get("identity_queries", [])),
                    "identity_results_count": int(snapshot.get("identity_results_count", 0)),
                    "sources_found": int(snapshot.get("sources_found", 0)),
                    "sources_ingested": int(snapshot.get("sources_ingested", 0)),
                    "rounds": int(snapshot.get("rounds_completed", 0)),
                    "trace": [],
                    "remaining_information_gaps": [],
                    "stop_reason": "failed_before_research_completed",
                    "current_stage": str(snapshot.get("current_stage", "unknown")),
                    "search_provider": str(snapshot.get("search_provider", "unknown")),
                }
                if snapshot.get("current_stage") == "entity_validation_failed":
                    review["entity"]["resolution_status"] = "validation_failed"
                elif snapshot.get("current_stage") in {
                    "entity_resolution_completed",
                    "identity_relevance_gate_started",
                    "relevance_gate_started",
                    "source_ingestion_started",
                    "source_ingestion_completed",
                }:
                    review["entity"]["resolution_status"] = "resolved"
        if stage == "technology_semantic" and semantic_processor is not None:
            review["semantic_execution"] = dict(semantic_processor.last_execution_trace)
        if stage == "technology_finance" and finance_processor is not None:
            review["finance_execution"] = dict(finance_processor.last_execution_trace)
        if stage == "evidence_assertions" and "assertion_processor" in locals():
            review["assertion_execution"] = dict(assertion_processor.last_execution_trace)
        error_category = getattr(exc, "category", type(exc).__name__)
        error_record: dict[str, Any] = {"stage": stage, "category": str(error_category)}
        if stage == "technology_semantic" and semantic_processor is not None:
            error_record["semantic_substage"] = semantic_processor.last_execution_trace.get(
                "semantic_substage", "unknown"
            )
        if stage == "technology_finance" and finance_processor is not None:
            error_record["finance_substage"] = finance_processor.last_execution_trace.get(
                "finance_substage", "unknown"
            )
        diagnostics = getattr(exc, "diagnostics", None)
        if isinstance(diagnostics, list) and diagnostics:
            # StructuredModelError diagnostics are field-level, input-free and redacted.
            error_record["diagnostics"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key in {"attempt", "location", "type", "message"}
                    and isinstance(value, (str, int))
                }
                for item in diagnostics
                if isinstance(item, dict)
            ]
        review["errors"].append(error_record)
        review["run"]["status"] = "failed"
        review["quality_checks"] = run_quality_checks(
            (review.get("technology_semantic") or {}).get("profile"),
            (review.get("technology_finance") or {}).get("profile"),
            inventory,
            review.get("research", {}),
            getattr(company, "company_id", None),
            review.get("entity"),
        )
        review["design_findings"] = review["quality_checks"].get("design_findings", [])
    finally:
        review["run"]["completed_at"] = datetime.now(timezone.utc).isoformat()
        knowledge_base.close()
    (run_dir / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "review.md").write_text(render_review_markdown(review), encoding="utf-8")
    return review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enterprise_name")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-queries-per-round", type=int, default=5)
    parser.add_argument("--max-results-per-query", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, help="输出目录；默认 runtime/review/end_to_end/<run_id>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_rounds <= 10:
        parser.error("--max-rounds must be between 1 and 10")
    if not 1 <= args.max_queries_per_round <= 8:
        parser.error("--max-queries-per-round must be between 1 and 8")
    if not 1 <= args.max_results_per_query <= 20:
        parser.error("--max-results-per-query must be between 1 and 20")
    settings = get_settings()
    missing = missing_provider_categories(settings)
    if missing:
        print("REAL_SMOKE_NOT_RUN")
        print("Missing configuration categories: " + ", ".join(missing))
        return 2
    run_id = make_run_id()
    run_dir = choose_run_dir(args.output_dir, run_id)
    review = asyncio.run(execute_review(args, settings, run_dir, run_id))
    print(json.dumps({"status": review["run"]["status"], "run_dir": str(run_dir), "review_md": str(run_dir / "review.md"), "review_json": str(run_dir / "review.json")}, ensure_ascii=False))
    return 0 if review["run"]["status"] in {"completed", "entity_not_resolved"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
