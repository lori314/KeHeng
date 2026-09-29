"""Registry-first technology-finance mapping with LLM-bounded evidence selection."""

from pathlib import Path

from app.finance.contracts import MappingSelection, TechFinanceProfile
from app.finance.registry import FinanceRegistry
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.structured_call import complete_contract
from app.llm import StructuredJSONModel


class TechnologyFinanceMapper:
    def __init__(self, model: StructuredJSONModel, registry: FinanceRegistry, prompt_path: str | Path | None = None, knowledge_registry: KnowledgeSemanticRegistry | None = None):
        self.model, self.registry = model, registry
        self.knowledge_registry = knowledge_registry or KnowledgeSemanticRegistry()
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "tech_finance_mapper_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")

    async def map(self, technology: TechnologySemanticProfile, financial_facts: list, gaps: list[str], processor_version: str) -> TechFinanceProfile:
        for rule in self.registry.rules:
            conditions = rule.applicable_conditions
            template_id = conditions.get("template_id")
            if template_id is not None:
                if template_id not in self.knowledge_registry.template_by_id:
                    raise ValueError(f"finance rule {rule.rule_id} references unknown technology template")
                template = self.knowledge_registry.template_by_id[template_id]
                known_milestones = {x.id for x in template.milestones}
                if not set(conditions.get("milestone_any", [])).issubset(known_milestones):
                    raise ValueError(f"finance rule {rule.rule_id} references unknown technology milestone")
            for node in rule.monitoring_nodes:
                template_id = node.get("related_template")
                if template_id not in self.knowledge_registry.template_by_id:
                    raise ValueError(f"finance rule {rule.rule_id} monitoring node references unknown template")
                known_milestones = {x.id for x in self.knowledge_registry.template_by_id[template_id].milestones}
                if not set(node.get("triggering_milestone_ids", [])).issubset(known_milestones):
                    raise ValueError(f"finance rule {rule.rule_id} monitoring node references unknown milestone")
        candidates = []
        facts_by_id = {x.fact_id: x for x in technology.technology_facts}
        financial_by_id = {x.fact_id: x for x in financial_facts}
        milestones = {(x.template_id, x.milestone_id): x for x in technology.milestone_observations}
        for rule in self.registry.rules:
            conditions = rule.applicable_conditions
            matching_observations = [
                (key, obs) for key, obs in milestones.items()
                if (not conditions.get("template_id") or key[0] == conditions["template_id"])
                and (not conditions.get("milestone_any") or key[1] in conditions["milestone_any"])
                and obs.status in {"supported", "limited_support", "conflict"}
            ]
            milestone_refs = [f"{tpl}:{mid}" for (tpl, mid), _ in matching_observations]
            tech_ids = list(dict.fromkeys(
                fact_id
                for _, observation in matching_observations
                for fact_id in observation.supporting_fact_ids + observation.contradicting_fact_ids
                if fact_id in facts_by_id
            ))
            dimension_filter = conditions.get("financial_dimension_any")
            fin_ids = [fact.fact_id for fact in financial_facts if not dimension_filter or fact.financial_dimension in dimension_filter]
            if (tech_ids or fin_ids) and (not conditions.get("template_id") or milestone_refs):
                candidates.append({"rule": rule.model_dump(mode="json"), "technology_fact_ids": tech_ids, "milestone_refs": milestone_refs, "financial_fact_ids": fin_ids})
        selected = MappingSelection(selections=[])
        if candidates:
            selected = await complete_contract(self.model, self.prompt, {"candidates": candidates, "available_technology_fact_ids": list(facts_by_id), "available_financial_fact_ids": list(financial_by_id), "available_milestone_refs": list(milestones), "allowed_rule_ids": [x["rule"]["rule_id"] for x in candidates], "allowed_scenario_ids": list(self.registry.scenario_by_id)}, MappingSelection, stage="technology-finance mapper")
        return self._assemble(technology, financial_facts, gaps, processor_version, selected, candidates, facts_by_id, financial_by_id, milestones)

    @staticmethod
    def _milestone_hints(template_id: str | None, milestone_id: str) -> list[str]:
        # The selection step is constrained by the actual profile milestone; template rule IDs and milestone IDs are checked again below.
        return [milestone_id]

    def _assemble(self, technology, financial_facts, gaps, processor_version, selected, candidates, tech_by_id, fin_by_id, milestones):
        from datetime import datetime, timezone
        from app.finance.contracts import EvidenceBundle, FinancialInformationGap, FinancingActivityObservation, FinanceRiskObservation, MonitoringNode
        candidate_by_rule = {entry["rule"]["rule_id"]: entry for entry in candidates}
        activities, risks, monitors, rules, scenarios = [], [], [], [], []
        all_gap_ids = set()
        for selection in selected.selections:
            entry = candidate_by_rule.get(selection.rule_id)
            if entry is None:
                raise ValueError(f"mapping returned rule outside applicable registry candidates: {selection.rule_id}")
            rule = self.registry.rule_by_id[selection.rule_id]
            if selection.scenario_id and selection.scenario_id not in self.registry.scenario_by_id:
                raise ValueError(f"mapping returned unknown scenario: {selection.scenario_id}")
            if selection.scenario_id != rule.scenario_id:
                raise ValueError(f"scenario does not match registry rule {rule.rule_id}")
            if not set(selection.observation_kinds).issubset(set(rule.output_type)):
                raise ValueError(f"mapping selected output not allowed by rule {rule.rule_id}")
            if not set(selection.technology_fact_ids).issubset(entry["technology_fact_ids"]) or not set(selection.technology_fact_ids).issubset(tech_by_id):
                raise ValueError("mapping returned an unsupported technology fact reference")
            if not set(selection.financial_fact_ids).issubset(entry["financial_fact_ids"]) or not set(selection.financial_fact_ids).issubset(fin_by_id):
                raise ValueError("mapping returned an unsupported financial fact reference")
            if not set(selection.milestone_refs).issubset(entry["milestone_refs"]):
                raise ValueError("mapping returned an unsupported milestone reference")
            if not selection.technology_fact_ids and not selection.financial_fact_ids:
                raise ValueError("mapping selection must cite source facts")
            rules.append(rule.rule_id)
            if rule.scenario_id:
                scenarios.append(rule.scenario_id)
            bundle = EvidenceBundle(technology_fact_ids=selection.technology_fact_ids, milestone_refs=selection.milestone_refs, financial_fact_ids=selection.financial_fact_ids, rule_ids=[rule.rule_id])
            weak_finance = any(fin_by_id[x].source_quality.category == "snippet_only" for x in selection.financial_fact_ids)
            linked_tech_qualities = [tech_by_id[x].source_quality.category for x in selection.technology_fact_ids]
            weak_technology = bool(linked_tech_qualities) and all(x in {"snippet_only", "weak_web"} for x in linked_tech_qualities)
            conflicted_refs = [
                ref for ref in selection.milestone_refs
                if milestones[tuple(ref.split(":", 1))].status == "conflict"
            ]
            incomplete_conflict = any(
                not set(milestones[tuple(ref.split(":", 1))].supporting_fact_ids).issubset(selection.technology_fact_ids)
                or not set(milestones[tuple(ref.split(":", 1))].contradicting_fact_ids).issubset(selection.technology_fact_ids)
                for ref in conflicted_refs
            )
            status = "conflict" if conflicted_refs and not incomplete_conflict else ("limited_support" if weak_finance or weak_technology or conflicted_refs else "supported")
            if "funding_activity" in selection.observation_kinds and rule.funding_activities:
                activities.append(FinancingActivityObservation(status=status, scenario_id=rule.scenario_id, funding_activities=rule.funding_activities, reason=rule.description, evidence_bundle=bundle))
            if "risk" in selection.observation_kinds and rule.risk_theme:
                risks.append(FinanceRiskObservation(status=status, risk_theme=rule.risk_theme, reason=rule.risk_reason or rule.description, missing_information=rule.gap_dimensions, evidence_bundle=bundle))
            if "monitoring" in selection.observation_kinds:
                for item in rule.monitoring_nodes:
                    monitors.append(MonitoringNode(**item, status=status, evidence_bundle=bundle))
            all_gap_ids.update(rule.gap_dimensions)
        present_dimensions = {x.financial_dimension for x in financial_facts}
        for fact in technology.technology_facts:
            if fact.source_quality.category == "snippet_only":
                continue
        all_gap_ids.update(x.id for x in self.registry.due_diligence_dimensions if x.id not in present_dimensions)
        info_gaps = [FinancialInformationGap(dimension_id=dimension, description=(self.registry.dimension_by_id[dimension].label + "信息未出现在当前来源中；缺失不表示该事项不存在或表现不佳。") if dimension in self.registry.dimension_by_id else f"{dimension} 信息未出现在当前来源中；缺失不表示该事项不存在或表现不佳。", requested_fields=[dimension], source_rule_ids=list(rules)) for dimension in sorted(all_gap_ids)]
        info_gaps.extend(FinancialInformationGap(dimension_id="additional_information_gap", description=f"来源资料缺口提示：{gap[:400]}", requested_fields=[], source_rule_ids=[]) for gap in dict.fromkeys(gaps) if gap.strip())
        stage = next((f"{obs.template_id}:{obs.milestone_id}" for obs in technology.milestone_observations if obs.status == "supported"), None)
        import hashlib
        import json
        key = [technology.company_id, technology.profile_id, processor_version, self.registry.registry_version, sorted(x.fact_id for x in financial_facts)]
        profile_id = "tfp_" + hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()[:32]
        return TechFinanceProfile(profile_id=profile_id, company_id=technology.company_id, company_name=technology.company_name, technology_profile_id=technology.profile_id, technology_stage=stage, enterprise_lifecycle="unknown", financial_facts=financial_facts, financial_scenarios=list(dict.fromkeys(scenarios)), funding_activities=activities, risk_observations=risks, monitoring_nodes=monitors, financial_information_gaps=info_gaps, applicable_rule_ids=list(dict.fromkeys(rules)), processor_version=processor_version, finance_registry_version=self.registry.registry_version, created_at=datetime.now(timezone.utc))
