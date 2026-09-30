"""Build deterministic evidence assertions from persisted semantic fact profiles."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.finance.contracts import TechFinanceProfile
from app.knowledge.assertions.contracts import EvidenceAssertion, EvidenceAssertionProfile
from app.knowledge.assertions.normalizer import (
    claim_equivalent,
    normalize_financial_amount,
    normalize_claim_text,
    normalize_period,
    normalize_subject,
    normalize_text,
    quality_rank,
)
from app.knowledge.semantic.contracts import TechnologySemanticProfile


PROCESSOR_VERSION = "evidence-assertions.v2"
FUZZY_CLUSTER_WARNING_MEMBER_COUNT = 8

_GROUPING_STRENGTH = {
    "exact": 0,
    "normalized_quantitative": 0,
    "containment": 1,
    "fuzzy_text": 2,
    "conflict_slot": 3,
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _quality(fact) -> str:
    return fact.source_quality.category


def _full_content(fact) -> int:
    return 0 if fact.source_quality.content_scope == "full_content" else 1


def _fact_order(fact) -> tuple:
    return quality_rank(_quality(fact)), _full_content(fact), fact.citation.source_id, fact.fact_id


def _representative(facts: list) -> object:
    return min(facts, key=_fact_order)


def _event_bucket(value) -> str:
    return value.isoformat() if value else ""


def _explicit_period(value: str | None, normalized: str) -> bool:
    if not value:
        return False
    # Canonical periods are explicit year, half-year, or quarter coordinates.
    return bool(re.fullmatch(r"(?:19|20)\d{2}(?:-H[12]|-Q[1-4])?", normalized))


def _text_claim_signature(domain: str, assertion_type: str, subject: str, period_or_event: str, object_value: str) -> list[str]:
    """Internal, deterministic fuzzy-text signature anchored to one representative."""
    return [domain, assertion_type, subject, period_or_event, normalize_claim_text(object_value)]


class EvidenceAssertionProcessor:
    def __init__(self, knowledge_base, *, processor_version: str = PROCESSOR_VERSION):
        self.knowledge_base = knowledge_base
        self.repository = knowledge_base.repository
        self.processor_version = processor_version
        self.last_execution_trace: dict[str, object] = {"stage": "not_started"}
        self._diagnostics: Counter[str] = Counter()
        self._fuzzy_cluster_ids: list[str] = []

    def process_company(self, company_id: str) -> EvidenceAssertionProfile:
        self._diagnostics = Counter()
        self._fuzzy_cluster_ids = []
        self.last_execution_trace = {"stage": "input_loading", "processor_version": self.processor_version}
        try:
            company = self.repository.get_company(company_id)
            if company is None:
                raise ValueError("Validated Company identity is required for assertion processing")
            raw_technology = self.repository.get_technology_semantic_profile(company_id)
            if raw_technology is None:
                raise ValueError("TechnologySemanticProfile is required for assertion processing")
            technology = TechnologySemanticProfile.model_validate(raw_technology)
            raw_finance = self.repository.get_technology_finance_profile(
                company_id, technology_profile_id=technology.profile_id
            )
            if raw_finance is None:
                raise ValueError("TechFinanceProfile is required for assertion processing")
            finance = TechFinanceProfile.model_validate(raw_finance)
            if finance.technology_profile_id != technology.profile_id:
                raise ValueError("Finance profile does not reference latest TechnologySemanticProfile")

            assertions = self._technology_assertions(company_id, company, technology.technology_facts)
            assertions.extend(self._finance_assertions(company_id, company, finance.financial_facts))
            assertions.sort(key=lambda item: (item.assertion_domain, item.normalization_key, item.assertion_id))
            profile_id = _digest(
                f"{company_id}\x1f{technology.profile_id}\x1f{finance.profile_id}\x1f{self.processor_version}"
            )
            suspicious = [
                assertion.assertion_id
                for assertion in assertions
                if assertion.grouping_method == "fuzzy_text"
                and len(assertion.member_fact_ids) > FUZZY_CLUSTER_WARNING_MEMBER_COUNT
            ]
            warnings = [f"suspicious_fuzzy_cluster:{item}" for item in suspicious]
            profile = EvidenceAssertionProfile(
                profile_id=profile_id,
                company_id=company_id,
                technology_profile_id=technology.profile_id,
                finance_profile_id=finance.profile_id,
                assertions=assertions,
                technology_assertion_count=sum(a.assertion_domain == "technology" for a in assertions),
                financial_assertion_count=sum(a.assertion_domain == "finance" for a in assertions),
                single_source_count=sum(a.evidence_status == "single_source" for a in assertions),
                multi_source_support_count=sum(a.evidence_status == "multi_source_support" for a in assertions),
                conflict_count=sum(a.evidence_status == "conflict" for a in assertions),
                company_subject_normalized_count=self._diagnostics["company_subject_normalized_count"],
                period_normalized_count=self._diagnostics["period_normalized_count"],
                exact_merge_count=self._diagnostics["exact_merge_count"],
                quantitative_merge_count=self._diagnostics["quantitative_merge_count"],
                containment_merge_count=self._diagnostics["containment_merge_count"],
                fuzzy_merge_count=self._diagnostics["fuzzy_merge_count"],
                numeric_guard_rejection_count=self._diagnostics["numeric_guard_rejection_count"],
                model_anchor_guard_rejection_count=self._diagnostics["model_anchor_guard_rejection_count"],
                candidate_pair_count=self._diagnostics["candidate_pair_count"],
                merged_fact_count=self._diagnostics["merged_fact_count"],
                suspicious_fuzzy_cluster_count=len(suspicious),
                warnings=warnings,
                processor_version=self.processor_version,
                created_at=datetime.now(timezone.utc),
            )
            self.last_execution_trace = {
                "stage": "complete",
                "status": "completed",
                "atomic_technology_fact_count": len(technology.technology_facts),
                "atomic_financial_fact_count": len(finance.financial_facts),
                "assertion_count": len(assertions),
                "technology_assertion_count": profile.technology_assertion_count,
                "financial_assertion_count": profile.financial_assertion_count,
                "single_source_count": profile.single_source_count,
                "multi_source_support_count": profile.multi_source_support_count,
                "conflict_count": profile.conflict_count,
                "company_subject_normalized_count": profile.company_subject_normalized_count,
                "period_normalized_count": profile.period_normalized_count,
                "exact_merge_count": profile.exact_merge_count,
                "quantitative_merge_count": profile.quantitative_merge_count,
                "containment_merge_count": profile.containment_merge_count,
                "fuzzy_merge_count": profile.fuzzy_merge_count,
                "numeric_guard_rejection_count": profile.numeric_guard_rejection_count,
                "model_anchor_guard_rejection_count": profile.model_anchor_guard_rejection_count,
                "candidate_pair_count": profile.candidate_pair_count,
                "merged_fact_count": profile.merged_fact_count,
                "suspicious_fuzzy_cluster_count": profile.suspicious_fuzzy_cluster_count,
                "warnings": warnings,
            }
            self.repository.save_evidence_assertion_profile(profile)
            return profile
        except Exception as exc:
            self.last_execution_trace = {
                **self.last_execution_trace,
                "stage": "failed",
                "status": "failed",
                "error_category": type(exc).__name__,
            }
            raise

    def _normal_subject(self, subject: str, company) -> str:
        normalized = normalize_subject(subject, company.canonical_name, company.aliases)
        if normalized == "__company__":
            self._diagnostics["company_subject_normalized_count"] += 1
        return normalized

    def _make_assertion(
        self,
        company_id: str,
        domain: str,
        assertion_type: str,
        key,
        facts: list,
        *,
        grouping_method: str,
        similarity_score: float | None = None,
        conflicts: list[str] | None = None,
    ) -> EvidenceAssertion:
        facts = sorted(facts, key=lambda item: item.fact_id)
        sources = sorted({fact.citation.source_id for fact in facts})
        qualities = Counter(_quality(fact) for fact in facts)
        evidence_status = "conflict" if conflicts else ("multi_source_support" if len(sources) > 1 else "single_source")
        representative = _representative(facts)
        normalized_key = json.dumps(
            [company_id, domain, key], ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        assertion_id = _digest(normalized_key)
        return EvidenceAssertion(
            assertion_id=assertion_id,
            company_id=company_id,
            assertion_domain=domain,
            assertion_type=assertion_type,
            representative_fact_id=representative.fact_id,
            member_fact_ids=[fact.fact_id for fact in facts],
            supporting_source_ids=sources,
            supporting_source_count=len(sources),
            evidence_status=evidence_status,
            source_quality_distribution=dict(sorted(qualities.items())),
            strongest_source_quality=_quality(representative),
            conflict_fact_ids=sorted(conflicts or []),
            grouping_method=grouping_method,
            similarity_score=similarity_score,
            normalization_key=normalized_key,
            processor_version=self.processor_version,
        )

    def _cluster_text_facts(
        self,
        facts: list,
        *,
        threshold: float,
        industry_or_core_business: bool = False,
        financing_without_period: bool = False,
    ) -> list[tuple[list, str, float | None]]:
        """Use quality-ranked deterministic anchors; never chain through members."""
        clusters: list[dict] = []
        for fact in sorted(facts, key=_fact_order):
            assigned = False
            for cluster in clusters:
                anchor = cluster["facts"][0]
                self._diagnostics["candidate_pair_count"] += 1
                mode, similarity, numeric_rejected, model_rejected = claim_equivalent(
                    anchor.object_value,
                    fact.object_value,
                    anchor.predicate,
                    fact.predicate,
                    threshold=threshold,
                    industry_or_core_business=industry_or_core_business,
                    financing_without_period=financing_without_period,
                )
                self._diagnostics["numeric_guard_rejection_count"] += int(numeric_rejected)
                self._diagnostics["model_anchor_guard_rejection_count"] += int(model_rejected)
                if mode is None:
                    continue
                cluster["facts"].append(fact)
                current_mode = cluster["method"]
                cluster["method"] = max((current_mode, mode), key=lambda value: _GROUPING_STRENGTH[value])
                cluster["similarities"].append(similarity)
                self._diagnostics[f"{mode.split('_')[0]}_merge_count" if mode in {"exact", "containment", "fuzzy_text"} else "fuzzy_merge_count"] += 1
                self._diagnostics["merged_fact_count"] += 1
                assigned = True
                break
            if not assigned:
                clusters.append({"facts": [fact], "method": "exact", "similarities": []})
        return [
            (cluster["facts"], cluster["method"], min(cluster["similarities"]) if cluster["similarities"] else None)
            for cluster in clusters
        ]

    def _technology_assertions(self, company_id: str, company, facts: list) -> list[EvidenceAssertion]:
        slots: dict[tuple, list] = defaultdict(list)
        for fact in facts:
            subject = self._normal_subject(fact.subject, company)
            slot = (fact.fact_type, subject, _event_bucket(fact.event_time))
            slots[slot].append(fact)
        result = []
        for slot, slot_facts in sorted(slots.items()):
            numeric = [f for f in slot_facts if f.quantitative_value is not None and f.quantitative_unit]
            numeric_ids = {f.fact_id for f in numeric}
            others = [f for f in slot_facts if f.fact_id not in numeric_ids]
            numeric_groups: dict[tuple, list] = defaultdict(list)
            for fact in numeric:
                value_key = (round(fact.quantitative_value, 8), normalize_text(fact.quantitative_unit))
                numeric_groups[value_key].append(fact)
            if numeric:
                self._diagnostics["candidate_pair_count"] += len(numeric) * (len(numeric) - 1) // 2
            for group in numeric_groups.values():
                merged = max(0, len(group) - 1)
                self._diagnostics["quantitative_merge_count"] += merged
                self._diagnostics["merged_fact_count"] += merged
            comparable_numeric = len(numeric_groups) > 1 and len({normalize_text(f.quantitative_unit) for f in numeric}) == 1
            conflict_ids = [f.fact_id for group in numeric_groups.values() for f in group] if comparable_numeric else []
            for value_key, group in sorted(numeric_groups.items(), key=lambda item: repr(item[0])):
                signature = ["technology", slot[0], slot[1], slot[2], "quantitative", value_key]
                result.append(self._make_assertion(
                    company_id, "technology", slot[0], signature, group,
                    grouping_method="conflict_slot" if comparable_numeric else "normalized_quantitative",
                    conflicts=conflict_ids,
                ))
            for group, method, similarity in self._cluster_text_facts(others, threshold=0.72):
                anchor = group[0]
                signature = _text_claim_signature("technology", slot[0], slot[1], slot[2], anchor.object_value)
                result.append(self._make_assertion(
                    company_id, "technology", slot[0], signature, group,
                    grouping_method=method, similarity_score=similarity,
                ))
        return result

    def _finance_assertions(self, company_id: str, company, facts: list) -> list[EvidenceAssertion]:
        slots: dict[tuple, list] = defaultdict(list)
        period_is_explicit: dict[tuple, bool] = {}
        for fact in facts:
            subject = self._normal_subject(fact.subject, company)
            normalized_period = normalize_period(fact.period)
            if fact.period and normalized_period != normalize_text(fact.period):
                self._diagnostics["period_normalized_count"] += 1
            event_time = _event_bucket(fact.event_time)
            if fact.period and _explicit_period(fact.period, normalized_period):
                period_key = f"period:{normalized_period}"
                explicit = True
            elif fact.period and event_time:
                period_key = f"event:{event_time}|period_raw:{normalized_period}"
                explicit = True
            elif fact.period:
                period_key = f"period_raw:{normalized_period}"
                explicit = False
            else:
                period_key = f"event:{event_time}" if event_time else ""
                explicit = bool(event_time)
            slot = (fact.financial_dimension, subject, period_key)
            slots[slot].append(fact)
            period_is_explicit[slot] = period_is_explicit.get(slot, False) or explicit
        result = []
        for slot, slot_facts in sorted(slots.items()):
            quantified = [f for f in slot_facts if f.quantitative_value is not None]
            nonquantified = [f for f in slot_facts if f.quantitative_value is None]
            amount_groups: dict[tuple, list] = defaultdict(list)
            for fact in quantified:
                amount = normalize_financial_amount(
                    fact.quantitative_value, fact.quantitative_unit, fact.currency
                )
                if amount is None:
                    amount = (
                        f"raw:{normalize_text(fact.quantitative_unit)}",
                        round(fact.quantitative_value, 8),
                        f"raw:{normalize_text(fact.currency)}",
                    )
                amount_groups[amount].append(fact)
            if quantified:
                self._diagnostics["candidate_pair_count"] += len(quantified) * (len(quantified) - 1) // 2
            for group in amount_groups.values():
                merged = max(0, len(group) - 1)
                self._diagnostics["quantitative_merge_count"] += merged
                self._diagnostics["merged_fact_count"] += merged
            amount_domains = {key[0] for key in amount_groups}
            conflict = (
                len(amount_groups) > 1
                and period_is_explicit.get(slot, False)
                and len(amount_domains) == 1
                and next(iter(amount_domains)) in {"CNY", "ratio"}
            )
            conflict_ids = [f.fact_id for group in amount_groups.values() for f in group] if conflict else []
            for value_key, group in sorted(amount_groups.items(), key=lambda item: repr(item[0])):
                signature = ["finance", slot[0], slot[1], slot[2], "amount", value_key]
                result.append(self._make_assertion(
                    company_id, "finance", slot[0], signature, group,
                    grouping_method="conflict_slot" if conflict else "normalized_quantitative",
                    conflicts=conflict_ids,
                ))

            no_period_financing = slot[0] == "financing" and not period_is_explicit.get(slot, False)
            broad_business_text = slot[0] in {"industry", "core_business"}
            threshold = 0.85 if broad_business_text else (0.95 if no_period_financing else 0.78)
            for group, method, similarity in self._cluster_text_facts(
                nonquantified,
                threshold=threshold,
                industry_or_core_business=broad_business_text,
                financing_without_period=no_period_financing,
            ):
                anchor = group[0]
                signature = _text_claim_signature("finance", slot[0], slot[1], slot[2], anchor.object_value)
                result.append(self._make_assertion(
                    company_id, "finance", slot[0], signature, group,
                    grouping_method=method, similarity_score=similarity,
                ))
        return result
