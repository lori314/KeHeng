"""Semantic and structural validation for joint technology-finance profiles."""

from app.finance.contracts import ReasoningGuard, TechFinanceProfile
from app.finance.registry import FinanceRegistry
from app.knowledge.semantic.contracts import TechnologySemanticProfile


class TechFinanceValidator:
    def __init__(self, registry: FinanceRegistry):
        self.registry = registry

    def validate(self, profile: TechFinanceProfile, technology: TechnologySemanticProfile) -> TechFinanceProfile:
        if profile.technology_profile_id != technology.profile_id:
            raise ValueError("finance profile references a different technology profile")
        tech_ids = {x.fact_id for x in technology.technology_facts}
        fin_ids = {x.fact_id for x in profile.financial_facts}
        milestone_refs = {f"{x.template_id}:{x.milestone_id}" for x in technology.milestone_observations}
        for fact in profile.financial_facts:
            if fact.company_id != profile.company_id:
                raise ValueError("financial fact company_id mismatch")
        for observation in [*profile.funding_activities, *profile.risk_observations, *profile.monitoring_nodes]:
            bundle = observation.evidence_bundle
            if not set(bundle.technology_fact_ids).issubset(tech_ids) or not set(bundle.financial_fact_ids).issubset(fin_ids) or not set(bundle.milestone_refs).issubset(milestone_refs):
                raise ValueError("observation references unknown source evidence")
            if not set(bundle.rule_ids).issubset(self.registry.rule_by_id):
                raise ValueError("observation references rule outside finance registry")
        if not set(profile.applicable_rule_ids).issubset(self.registry.rule_by_id):
            raise ValueError("profile references unknown finance rule")
        if not set(profile.financial_scenarios).issubset(self.registry.scenario_by_id):
            raise ValueError("profile references unknown finance scenario")
        ReasoningGuard.validate_payload(profile.model_dump(mode="json"))
        return profile
