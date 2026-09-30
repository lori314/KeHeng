"""Deterministic assembly of source-traceable V2 evidence reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.finance.contracts import TechFinanceProfile
from app.finance.registry import FinanceRegistry
from app.knowledge.assertions.contracts import EvidenceAssertionProfile
from app.knowledge.contracts import Company, KnowledgeLayer
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.report_v2.contracts import (
    EvidenceFirstReport,
    ReportAssertion,
    ReportCitation,
    ReportEvidenceBundle,
    ReportFact,
)

REPORT_VERSION = "evidence-first-report.v1"
_QUALITY_RANK = {
    "authoritative_public_record": 0,
    "first_party": 1,
    "academic_or_patent": 2,
    "third_party": 3,
    "weak_web": 4,
    "snippet_only": 5,
}
_STATUS_LABELS = {
    "supported": "证据支持",
    "limited_support": "有限支持",
    "conflict": "证据冲突",
    "no_evidence": "暂无证据",
    "insufficient_evidence": "证据不足",
}
_SYSTEM_GAP_MARKERS = ("selector", "batch failed", "processing warning", "处理警告", "选择器")


class EvidenceFirstReportAssembler:
    """Load persisted profile chain and assemble a deterministic report."""

    def __init__(
        self,
        repository,
        *,
        finance_registry: FinanceRegistry | None = None,
        semantic_registry: KnowledgeSemanticRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.finance_registry = finance_registry or FinanceRegistry()
        self.semantic_registry = semantic_registry or KnowledgeSemanticRegistry()

    def build(self, company_id: str) -> EvidenceFirstReport:
        company = self.repository.get_company(company_id)
        if company is None:
            raise ValueError("missing_company")
        tech_data = self.repository.get_technology_semantic_profile(company_id)
        if tech_data is None:
            raise ValueError("missing_technology_profile")
        tech = TechnologySemanticProfile.model_validate(tech_data)
        finance_data = self.repository.get_technology_finance_profile(
            company_id, technology_profile_id=tech.profile_id
        )
        if finance_data is None:
            finance_data = self.repository.get_technology_finance_profile(company_id)
            if finance_data is None:
                raise ValueError("missing_finance_profile")
        finance = TechFinanceProfile.model_validate(finance_data)
        if finance.technology_profile_id != tech.profile_id:
            raise ValueError("stale_profile_chain")
        assertion_data = self.repository.get_evidence_assertion_profile(
            company_id,
            technology_profile_id=tech.profile_id,
            finance_profile_id=finance.profile_id,
        )
        if assertion_data is None:
            assertion_data = self.repository.get_evidence_assertion_profile(company_id)
            if assertion_data is None:
                raise ValueError("missing_assertion_profile")
        assertions = EvidenceAssertionProfile.model_validate(assertion_data)
        if (
            assertions.technology_profile_id != tech.profile_id
            or assertions.finance_profile_id != finance.profile_id
        ):
            raise ValueError("stale_profile_chain")
        if tech.company_id != company_id or finance.company_id != company_id or assertions.company_id != company_id:
            raise ValueError("stale_profile_chain")
        return self._assemble(company, tech, finance, assertions)

    def _assemble(self, company, tech, finance, assertions) -> EvidenceFirstReport:
        tech_by_id = {fact.fact_id: fact for fact in tech.technology_facts}
        finance_by_id = {fact.fact_id: fact for fact in finance.financial_facts}
        tech_facts = {key: self._report_fact(value) for key, value in tech_by_id.items()}
        finance_facts = {key: self._report_fact(value) for key, value in finance_by_id.items()}

        template_ids = list(tech.template_selection.selected_template_ids)
        template_by_id = self.semantic_registry.templates
        domain_by_id = self.semantic_registry.domains
        template_names = {item.id: item.name for item in template_by_id.templates}
        domain_names = {item.id: item for item in domain_by_id.domains}

        technology_quality = Counter(f.source_quality.category for f in tech.technology_facts)
        technology_types = Counter(f.fact_type for f in tech.technology_facts)
        representative_technology = self._representatives(
            tech.technology_facts, tech_facts, lambda fact: fact.fact_type
        )
        milestones, milestone_lookup = self._milestones(tech, tech_facts, template_ids)
        financial_dimensions = self._financial_dimensions(finance, finance_facts)
        financial_quality = Counter(f.source_quality.category for f in finance.financial_facts)
        representative_assertions = self._representative_assertions(
            assertions, tech_facts, finance_facts
        )
        finance_links = self._finance_links(
            finance, tech_facts, finance_facts, milestone_lookup
        )
        financial_gaps = self._financial_gaps(finance)
        semantic_gaps = self._semantic_gaps(tech.information_gaps)
        sources = self._source_summary(company, tech, finance)
        warning_list = list(tech.processing_warnings)
        website_verified = self._official_website_verified(company)
        if not website_verified:
            warning_list.append(
                "当前未取得企业官网的强自证验证；普通 web 来源不会因此升级为 first-party。"
            )
        report_id = self._report_id(
            company.company_id or "", tech.profile_id, finance.profile_id, assertions.profile_id
        )
        return EvidenceFirstReport(
            report_id=report_id,
            company_id=company.company_id or "",
            company_name=company.canonical_name,
            generated_at=datetime.now(timezone.utc),
            report_version=REPORT_VERSION,
            company_overview={
                "canonical_name": company.canonical_name,
                "aliases": company.aliases,
                "resolution_status": str(getattr(company.resolution_status, "value", company.resolution_status)),
                "official_website": company.official_website if website_verified else None,
                "primary_domain_ids": tech.domain_profile.primary_domains,
                "primary_domain_names": [domain_names[x].name for x in tech.domain_profile.primary_domains if x in domain_names],
                "selected_template_ids": template_ids,
                "selected_template_names": [template_names[x] for x in template_ids if x in template_names],
            },
            technology_profile={
                "fact_count": len(tech.technology_facts),
                "fact_type_distribution": dict(sorted(technology_types.items())),
                "strongest_evidence_quality_distribution": dict(sorted(technology_quality.items())),
                "representative_facts": representative_technology,
                "all_fact_ids": sorted(tech_by_id),
            },
            technology_milestones=milestones,
            evidence_assertions={
                "atomic_fact_count": len(tech_by_id) + len(finance_by_id),
                "assertion_count": len(assertions.assertions),
                "technology_assertion_count": assertions.technology_assertion_count,
                "financial_assertion_count": assertions.financial_assertion_count,
                "single_source_count": assertions.single_source_count,
                "multi_source_support_count": assertions.multi_source_support_count,
                "conflict_count": assertions.conflict_count,
                "compression_ratio": round((len(tech_by_id) + len(finance_by_id)) / len(assertions.assertions), 4) if assertions.assertions else 0.0,
                "representative_assertions": representative_assertions,
            },
            financial_profile={
                "fact_count": len(finance.financial_facts),
                "dimension_distribution": dict(sorted(Counter(f.financial_dimension for f in finance.financial_facts).items())),
                "source_quality_distribution": dict(sorted(financial_quality.items())),
                "dimensions": financial_dimensions,
                "all_fact_ids": sorted(finance_by_id),
            },
            technology_finance_links=finance_links,
            information_gaps={
                "total_gap_count": len(finance.financial_information_gaps),
                "deduplicated_financial_dimension_count": len(financial_gaps),
                "financial_gaps": financial_gaps,
                "semantic_gaps": semantic_gaps[:10],
                "all_semantic_gaps": semantic_gaps,
            },
            source_summary=sources,
            methodology={
                "positioning": "本报告用于组织公开资料、技术证据与经营事实，辅助人工尽调和信息核验；不构成授信、投资或融资决策。",
                "evidence_boundary": [
                    "未检索到公开证据不代表事项不存在。",
                    "有限支持不表示里程碑已经发生。",
                    "多源支持不代表来源彼此独立。",
                    "weak_web 不作为强结论来源。",
                ],
                "processor_versions": {"technology": tech.processor_version, "finance": finance.processor_version, "assertions": assertions.processor_version},
                "registry_versions": {"technology_domain": tech.domain_registry_version, "technology_template": tech.template_registry_version, "finance": finance.finance_registry_version},
                "warnings": list(dict.fromkeys(warning_list)),
            },
        )

    def _report_fact(self, fact: Any) -> ReportFact:
        citation = fact.citation
        source = self.repository.get_source(citation.source_id)
        if source is None:
            raise ValueError("missing_source")
        locator = citation.locator
        return ReportFact(
            fact_id=fact.fact_id,
            fact_type=fact.fact_type,
            subject=fact.subject,
            predicate=fact.predicate,
            object_value=fact.object_value,
            period=getattr(fact, "period", None),
            event_time=fact.event_time,
            quantitative_value=fact.quantitative_value,
            quantitative_unit=fact.quantitative_unit,
            source_quality=fact.source_quality.category,
            evidence=ReportCitation(
                citation_id=citation.citation_id,
                source_id=source.source_id,
                source_type=source.source_type.value,
                source_title=citation.source_title or source.title,
                source_url=citation.source_url or source.canonical_url or locator.url,
                chunk_id=fact.source_chunk_id,
                excerpt=citation.excerpt,
                page_number=locator.page_number,
                paragraph_number=locator.paragraph_number,
                locator_text=locator.locator_text or locator.heading or locator.section or locator.text_anchor,
                source_quality=fact.source_quality.category,
            ),
        )

    def _representatives(self, facts, mapped, group_key):
        groups: dict[str, list[Any]] = {}
        for fact in facts:
            groups.setdefault(group_key(fact), []).append(fact)
        chosen = []
        for key in sorted(groups):
            ordered = sorted(groups[key], key=self._fact_sort_key)
            chosen.extend(mapped[f.fact_id] for f in ordered[:2])
        return chosen

    @staticmethod
    def _fact_sort_key(fact):
        return (
            _QUALITY_RANK.get(fact.source_quality.category, 99),
            0 if fact.source_quality.content_scope == "full_content" else 1,
            -(fact.event_time.timestamp() if fact.event_time else float("-inf")),
            fact.fact_id,
        )

    def _milestones(self, tech, fact_map, selected_template_ids):
        observations = {(x.template_id, x.milestone_id): x for x in tech.milestone_observations}
        result, lookup = [], {}
        for template_id in selected_template_ids:
            template = self.semantic_registry.template_by_id.get(template_id)
            if template is None:
                raise ValueError("unknown_template")
            for milestone in template.milestones:
                observation = observations.get((template_id, milestone.id))
                if observation is None:
                    continue
                support = self._resolve_ids(observation.supporting_fact_ids, fact_map, "missing_technology_fact")
                contradict = self._resolve_ids(observation.contradicting_fact_ids, fact_map, "missing_technology_fact")
                item = {
                    "template_id": template_id, "template_name": template.name,
                    "milestone_id": milestone.id, "milestone_name": milestone.label,
                    "status": observation.status, "status_label": _STATUS_LABELS[observation.status],
                    "reason": observation.reason, "supporting_facts": support,
                    "contradicting_facts": contradict, "blocked_inferences": observation.blocked_inferences,
                }
                result.append(item)
                lookup[f"{template_id}:{milestone.id}"] = item
        # Retain observations even if a template registry was updated after profile creation.
        for (template_id, milestone_id), observation in observations.items():
            if f"{template_id}:{milestone_id}" in lookup:
                continue
            template = self.semantic_registry.template_by_id.get(template_id)
            milestone = next((x for x in template.milestones if x.id == milestone_id), None) if template else None
            if milestone is None:
                raise ValueError("unknown_milestone")
            item = {
                "template_id": template_id, "template_name": template.name,
                "milestone_id": milestone.id, "milestone_name": milestone.label,
                "status": observation.status, "status_label": _STATUS_LABELS[observation.status],
                "reason": observation.reason,
                "supporting_facts": self._resolve_ids(observation.supporting_fact_ids, fact_map, "missing_technology_fact"),
                "contradicting_facts": self._resolve_ids(observation.contradicting_fact_ids, fact_map, "missing_technology_fact"),
                "blocked_inferences": observation.blocked_inferences,
            }
            result.append(item)
            lookup[f"{template_id}:{milestone_id}"] = item
        return result, lookup

    def _financial_dimensions(self, finance, fact_map):
        groups: dict[str, list[Any]] = {}
        for fact in finance.financial_facts:
            groups.setdefault(fact.financial_dimension, []).append(fact)
        output = []
        for dimension_id in sorted(groups):
            dimension = self.finance_registry.dimension_by_id.get(dimension_id)
            output.append({
                "dimension_id": dimension_id,
                "dimension_name": dimension.label if dimension else dimension_id,
                "fact_count": len(groups[dimension_id]),
                "representative_facts": [fact_map[f.fact_id] for f in sorted(groups[dimension_id], key=self._fact_sort_key)[:2]],
            })
        return output

    def _representative_assertions(self, profile, tech_facts, finance_facts):
        eligible = [a for a in profile.assertions if a.evidence_status in {"conflict", "multi_source_support"} or len(a.member_fact_ids) > 1]
        priority = {"conflict": 0, "multi_source_support": 1, "single_source": 2}
        eligible.sort(key=lambda a: (priority[a.evidence_status], -len(a.member_fact_ids), _QUALITY_RANK.get(a.strongest_source_quality, 99), a.assertion_id))
        result = []
        for assertion in eligible[:10]:
            members = self._resolve_ids(assertion.member_fact_ids, tech_facts if assertion.assertion_domain == "technology" else finance_facts, "missing_assertion_fact")
            representative = (tech_facts if assertion.assertion_domain == "technology" else finance_facts).get(assertion.representative_fact_id)
            if representative is None:
                raise ValueError("missing_assertion_fact")
            result.append({
                "assertion_id": assertion.assertion_id, "assertion_domain": assertion.assertion_domain,
                "assertion_type": assertion.assertion_type, "evidence_status": assertion.evidence_status,
                "grouping_method": assertion.grouping_method, "member_fact_count": len(assertion.member_fact_ids),
                "supporting_source_count": assertion.supporting_source_count,
                "strongest_source_quality": assertion.strongest_source_quality,
                "representative_fact": representative, "member_fact_ids": assertion.member_fact_ids,
            })
        return result

    def _finance_links(self, finance, tech_facts, finance_facts, milestones):
        def bundle(value):
            tech = self._resolve_ids(value.technology_fact_ids, tech_facts, "missing_technology_fact")
            fin = self._resolve_ids(value.financial_fact_ids, finance_facts, "missing_financial_fact")
            expanded = []
            for ref in value.milestone_refs:
                item = milestones.get(ref)
                if item is None:
                    raise ValueError("missing_milestone_reference")
                expanded.append({key: item[key] for key in ("template_id", "milestone_id", "milestone_name", "status", "status_label")})
            return ReportEvidenceBundle(technology_facts=tech, milestones=expanded, financial_facts=fin, rule_ids=value.rule_ids)

        rules = []
        for rule_id in finance.applicable_rule_ids:
            rule = self.finance_registry.rule_by_id.get(rule_id)
            if rule is None:
                raise ValueError("unknown_finance_rule")
            scenario = self.finance_registry.scenario_by_id.get(rule.scenario_id) if rule.scenario_id else None
            rules.append({"rule_id": rule_id, "rule_title": rule.title, "scenario_id": rule.scenario_id, "scenario_name": scenario.label if scenario else None})
        funding = [{"status": x.status, "status_label": _STATUS_LABELS[x.status], "scenario_id": x.scenario_id, "activities": x.funding_activities, "reason": x.reason, "evidence": bundle(x.evidence_bundle)} for x in finance.funding_activities]
        risks = [{"status": x.status, "status_label": _STATUS_LABELS[x.status], "risk_theme": x.risk_theme, "reason": x.reason, "missing_information": x.missing_information, "evidence": bundle(x.evidence_bundle)} for x in finance.risk_observations]
        monitors = []
        for x in finance.monitoring_nodes:
            explanation = {
                "supported": "触发这一监测关注点的证据达到支持状态。",
                "limited_support": "触发这一监测关注点的证据强度有限。",
                "conflict": "触发这一监测关注点的证据存在冲突。",
                "insufficient_evidence": "当前证据不足以判断这一监测关注点。",
            }[x.status]
            monitors.append({"node_id": x.node_id, "name": x.name, "status": x.status, "status_label": _STATUS_LABELS[x.status], "status_explanation": explanation, "reason": x.reason, "required_evidence_types": x.required_evidence_types, "triggering_milestones": x.triggering_milestone_ids, "evidence": bundle(x.evidence_bundle)})
        return {"applicable_rules": rules, "funding_activities": funding, "risks": risks, "monitoring_nodes": monitors}

    def _financial_gaps(self, finance):
        groups = {}
        for gap in finance.financial_information_gaps:
            entry = groups.setdefault(gap.dimension_id, {"descriptions": [], "requested": [], "rules": []})
            entry["descriptions"].append(gap.description)
            entry["requested"].extend(gap.requested_fields)
            entry["rules"].extend(gap.source_rule_ids)
        output = []
        for dimension_id in sorted(groups):
            values = groups[dimension_id]
            dim = self.finance_registry.dimension_by_id.get(dimension_id)
            output.append({"dimension_id": dimension_id, "dimension_name": dim.label if dim else dimension_id, "description": "；".join(dict.fromkeys(values["descriptions"])), "requested_fields": list(dict.fromkeys(values["requested"])), "source_rule_ids": list(dict.fromkeys(values["rules"]))})
        return output

    @staticmethod
    def _semantic_gaps(values):
        output = []
        for value in values:
            text = " ".join(value.split())
            lowered = re.sub(r"[_-]+", " ", text.casefold())
            if text and not any(marker in lowered for marker in _SYSTEM_GAP_MARKERS) and text not in output:
                output.append(text)
        return output

    def _source_summary(self, company, tech, finance):
        chunks = self.repository.list_current_chunks(company.company_id or "", KnowledgeLayer.GENERAL.value)
        by_source = {}
        for chunk in chunks:
            by_source.setdefault(chunk.source_id, chunk)
        type_counts, quality_counts = Counter(), Counter()
        quality_by_type = {
            "government": "authoritative_public_record", "regulatory": "authoritative_public_record", "registry": "authoritative_public_record", "standard": "authoritative_public_record", "exchange_disclosure": "authoritative_public_record",
            "company_official": "first_party", "paper": "academic_or_patent", "patent": "academic_or_patent", "news": "third_party", "web": "weak_web",
        }
        first_party = authoritative = weak = snippet = 0
        fact_quality_by_source = {}
        for fact in [*tech.technology_facts, *finance.financial_facts]:
            fact_quality_by_source.setdefault(fact.citation.source_id, fact.source_quality.category)
        for source_id, chunk in by_source.items():
            source = self.repository.get_source(source_id)
            if source is None:
                raise ValueError("missing_source")
            type_counts[source.source_type.value] += 1
            version = self.repository.get_current_source_version(source_id)
            scope = str(chunk.metadata.get("content_scope") or (version.metadata.get("content_scope") if version else "") or "full_content")
            quality = "snippet_only" if scope == "search_snippet" else fact_quality_by_source.get(source_id, quality_by_type.get(source.source_type.value, "weak_web"))
            quality_counts[quality] += 1
            first_party += quality == "first_party"
            authoritative += quality == "authoritative_public_record"
            weak += quality == "weak_web"
            snippet += quality == "snippet_only"
        return {"evidence_source_count": len(by_source), "source_type_distribution": dict(sorted(type_counts.items())), "source_quality_distribution": dict(sorted(quality_counts.items())), "first_party_count": first_party, "authoritative_public_record_count": authoritative, "weak_web_count": weak, "snippet_only_count": snippet, "official_website_verified": self._official_website_verified(company)}

    @staticmethod
    def _official_website_verified(company):
        metadata = company.metadata or {}
        return bool(
            company.official_website
            and metadata.get("official_website_evidence_url")
            and metadata.get("official_website_resolution_source") in {"model", "self_attested_page"}
        )

    @staticmethod
    def _resolve_ids(ids, mapping, error):
        resolved = []
        for item in ids:
            fact = mapping.get(item)
            if fact is None:
                raise ValueError(error)
            resolved.append(fact)
        return resolved

    @staticmethod
    def _report_id(company_id, tech_id, finance_id, assertion_id):
        payload = json.dumps([company_id, tech_id, finance_id, assertion_id, REPORT_VERSION], separators=(",", ":"))
        return "efr-" + hashlib.sha256(payload.encode()).hexdigest()[:24]
