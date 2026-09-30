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
        milestones = {
            (x.template_id, x.milestone_id): x
            for x in technology.milestone_observations
        }
        milestone_refs = {f"{template}:{milestone}" for template, milestone in milestones}
        for fact in profile.financial_facts:
            if fact.company_id != profile.company_id:
                raise ValueError("financial fact company_id mismatch")
        for observation in [*profile.funding_activities, *profile.risk_observations, *profile.monitoring_nodes]:
            bundle = observation.evidence_bundle
            if not set(bundle.technology_fact_ids).issubset(tech_ids) or not set(bundle.financial_fact_ids).issubset(fin_ids) or not set(bundle.milestone_refs).issubset(milestone_refs):
                raise ValueError("observation references unknown source evidence")
            if not set(bundle.rule_ids).issubset(self.registry.rule_by_id):
                raise ValueError("observation references rule outside finance registry")
            referenced_milestones = []
            for rule_id in bundle.rule_ids:
                rule = self.registry.rule_by_id[rule_id]
                conditions = rule.applicable_conditions
                allowed_milestones = set(conditions.get("milestone_any", []))
                required_template = conditions.get("template_id")
                if allowed_milestones and not bundle.milestone_refs:
                    raise ValueError("milestone-conditioned observation is missing milestone provenance")
                for ref in bundle.milestone_refs:
                    parts = ref.split(":", 1)
                    if len(parts) != 2:
                        raise ValueError("observation contains malformed milestone reference")
                    template_id, milestone_id = parts
                    if (
                        not allowed_milestones
                        or template_id != required_template
                        or milestone_id not in allowed_milestones
                    ):
                        raise ValueError("observation milestone does not match its finance rule trigger")
                    referenced_milestones.append(milestones[(template_id, milestone_id)])

            required_fact_ids = {
                fact_id
                for milestone in referenced_milestones
                for fact_id in milestone.supporting_fact_ids + milestone.contradicting_fact_ids
            }
            if not required_fact_ids.issubset(bundle.technology_fact_ids):
                raise ValueError("observation is missing technology facts supporting its milestone provenance")
            if observation.status == "supported" and any(
                milestone.status != "supported" for milestone in referenced_milestones
            ):
                raise ValueError("supported observation references a milestone that is not supported")
            if observation in profile.funding_activities and any(
                milestone.status != "supported" for milestone in referenced_milestones
            ):
                raise ValueError("funding activity references a milestone that is not supported")
        if not set(profile.applicable_rule_ids).issubset(self.registry.rule_by_id):
            raise ValueError("profile references unknown finance rule")
        if not set(profile.financial_scenarios).issubset(self.registry.scenario_by_id):
            raise ValueError("profile references unknown finance scenario")
        ReasoningGuard.validate_payload(profile.model_dump(mode="json"))
        return profile
