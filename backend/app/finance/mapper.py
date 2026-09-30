"""Deterministic registry-first technology-finance mapping."""

from app.finance.contracts import MappingSelection, TechFinanceProfile
from app.finance.registry import FinanceRegistry
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry


def _milestone_strength(refs, milestones, *, required: bool) -> str:
    """Derive finance evidence strength from the referenced technology milestones."""
    if not refs:
        return "insufficient_evidence" if required else "supported"
    observations = []
    for ref in refs:
        key = tuple(ref.split(":", 1))
        observation = milestones.get(key) if len(key) == 2 else None
        if observation is None:
            return "insufficient_evidence"
        observations.append(observation)
    statuses = {item.status for item in observations}
    if "conflict" in statuses:
        return "conflict"
    if "limited_support" in statuses:
        return "limited_support"
    if "no_evidence" in statuses:
        return "insufficient_evidence"
    return "supported" if statuses == {"supported"} else "insufficient_evidence"


class TechnologyFinanceMapper:
    def __init__(self, registry: FinanceRegistry, knowledge_registry: KnowledgeSemanticRegistry | None = None):
        self.registry = registry
        self.knowledge_registry = knowledge_registry or KnowledgeSemanticRegistry()
        self.last_execution_trace: dict[str, object] = {
            "mapping_mode": "deterministic_registry",
            "candidate_rule_count": 0,
            "candidate_rule_ids": [],
            "selected_rule_count": 0,
            "selected_rule_ids": [],
            "milestone_conditioned_candidate_count": 0,
            "general_candidate_count": 0,
        }

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
            requires_milestone = bool(conditions.get("milestone_any"))
            matching_observations = [
                (key, obs) for key, obs in milestones.items()
                if (not conditions.get("template_id") or key[0] == conditions["template_id"])
                and (not conditions.get("milestone_any") or key[1] in conditions["milestone_any"])
                and (obs.status == "supported" if requires_milestone else False)
            ]
            milestone_refs = [f"{tpl}:{mid}" for (tpl, mid), _ in matching_observations]
            milestone_candidates = [
                {"ref": ref, "status": observation.status}
                for ref, (_, observation) in zip(milestone_refs, matching_observations, strict=True)
            ]
            tech_ids = list(dict.fromkeys(
                fact_id
                for _, observation in matching_observations
                for fact_id in observation.supporting_fact_ids + observation.contradicting_fact_ids
                if fact_id in facts_by_id
            ))
            dimension_filter = conditions.get("financial_dimension_any")
            fin_ids = [fact.fact_id for fact in financial_facts if not dimension_filter or fact.financial_dimension in dimension_filter]
            if (tech_ids or fin_ids) and (not requires_milestone or milestone_refs):
                candidates.append({"rule": rule.model_dump(mode="json"), "technology_fact_ids": tech_ids, "milestone_refs": milestone_refs, "milestones": milestone_candidates, "financial_fact_ids": fin_ids})
        candidate_ids = list(dict.fromkeys(x["rule"]["rule_id"] for x in candidates))
        milestone_count = sum(bool(x["rule"]["applicable_conditions"].get("milestone_any")) for x in candidates)
        general_count = len(candidates) - milestone_count
        selections = [
            {
                "rule_id": entry["rule"]["rule_id"],
                "scenario_id": entry["rule"]["scenario_id"],
                "technology_fact_ids": list(dict.fromkeys(entry["technology_fact_ids"])),
                "milestone_refs": list(dict.fromkeys(entry["milestone_refs"])),
                "financial_fact_ids": list(dict.fromkeys(entry["financial_fact_ids"])),
                "observation_kinds": list(dict.fromkeys(entry["rule"]["output_type"])),
            }
            for entry in candidates
        ]
        selected = MappingSelection(selections=selections)
        self.last_execution_trace = {
            "mapping_mode": "deterministic_registry",
            "mapping_status": "completed",
            "candidate_rule_count": len(candidate_ids),
            "candidate_rule_ids": candidate_ids,
            "selected_rule_count": len(candidate_ids),
            "selected_rule_ids": candidate_ids,
            "milestone_conditioned_candidate_count": milestone_count,
            "general_candidate_count": general_count,
        }
        try:
            return self._assemble(technology, financial_facts, gaps, processor_version, selected, candidates, facts_by_id, financial_by_id, milestones)
        except Exception:
            self.last_execution_trace["mapping_status"] = "failed"
            raise

    def _assemble(self, technology, financial_facts, gaps, processor_version, selected, candidates, tech_by_id, fin_by_id, milestones):
        from datetime import datetime, timezone
        from app.finance.contracts import EvidenceBundle, FinancialInformationGap, FinancingActivityObservation, FinanceRiskObservation, MonitoringNode
        candidate_by_rule = {entry["rule"]["rule_id"]: entry for entry in candidates}
        activities, risks, monitors, rules, scenarios = [], [], [], [], []
        all_gap_ids = set()
        for selection in selected.selections:
            entry = candidate_by_rule.get(selection.rule_id)
            if entry is None:
                raise RuntimeError(f"deterministic mapping invariant violated: rule outside candidates: {selection.rule_id}")
            rule = self.registry.rule_by_id[selection.rule_id]
            if selection.scenario_id and selection.scenario_id not in self.registry.scenario_by_id:
                raise RuntimeError(f"deterministic mapping invariant violated: unknown scenario: {selection.scenario_id}")
            if selection.scenario_id != rule.scenario_id:
                raise RuntimeError(f"deterministic mapping invariant violated: scenario mismatch for {rule.rule_id}")
            if not set(selection.observation_kinds).issubset(set(rule.output_type)):
                raise RuntimeError(f"deterministic mapping invariant violated: output kind mismatch for {rule.rule_id}")
            if not set(selection.technology_fact_ids).issubset(entry["technology_fact_ids"]) or not set(selection.technology_fact_ids).issubset(tech_by_id):
                raise RuntimeError("deterministic mapping invariant violated: unsupported technology fact reference")
            if not set(selection.financial_fact_ids).issubset(entry["financial_fact_ids"]) or not set(selection.financial_fact_ids).issubset(fin_by_id):
                raise RuntimeError("deterministic mapping invariant violated: unsupported financial fact reference")
            if not set(selection.milestone_refs).issubset(entry["milestone_refs"]):
                raise RuntimeError("deterministic mapping invariant violated: unsupported milestone reference")
            conditions = rule.applicable_conditions
            requires_milestone = bool(conditions.get("milestone_any"))
            selected_milestone_refs = list(selection.milestone_refs)
            if requires_milestone and not selected_milestone_refs:
                selected_milestone_refs = list(entry["milestone_refs"])
            selected_milestone_refs = list(dict.fromkeys(selected_milestone_refs))
            required_milestone_fact_ids = list(dict.fromkeys(
                fact_id
                for ref in selected_milestone_refs
                for fact_id in (
                    milestones[tuple(ref.split(":", 1))].supporting_fact_ids
                    + milestones[tuple(ref.split(":", 1))].contradicting_fact_ids
                )
            ))
            if not set(required_milestone_fact_ids).issubset(entry["technology_fact_ids"]):
                raise RuntimeError("deterministic mapping invariant violated: candidate omitted milestone provenance facts")
            if not set(required_milestone_fact_ids).issubset(tech_by_id):
                raise RuntimeError("deterministic mapping invariant violated: milestone fact record missing")
            technology_fact_ids = list(dict.fromkeys(required_milestone_fact_ids + list(selection.technology_fact_ids)))
            if not technology_fact_ids and not selection.financial_fact_ids:
                raise RuntimeError("deterministic mapping invariant violated: selection has no source facts")
            rules.append(rule.rule_id)
            if rule.scenario_id:
                scenarios.append(rule.scenario_id)
            bundle = EvidenceBundle(technology_fact_ids=technology_fact_ids, milestone_refs=selected_milestone_refs, financial_fact_ids=selection.financial_fact_ids, rule_ids=[rule.rule_id])
            finance_qualities = [fin_by_id[x].source_quality.category for x in selection.financial_fact_ids]
            weak_finance = bool(finance_qualities) and all(x in {"snippet_only", "weak_web"} for x in finance_qualities)
            linked_tech_qualities = [tech_by_id[x].source_quality.category for x in technology_fact_ids]
            weak_technology = bool(linked_tech_qualities) and all(x in {"snippet_only", "weak_web"} for x in linked_tech_qualities)
            milestone_status = _milestone_strength(selected_milestone_refs, milestones, required=requires_milestone)
            if milestone_status in {"conflict", "limited_support", "insufficient_evidence"}:
                status = milestone_status
            elif weak_finance or weak_technology:
                status = "limited_support"
            else:
                status = "supported"
            if "funding_activity" in selection.observation_kinds and rule.funding_activities and milestone_status == "supported":
                activities.append(FinancingActivityObservation(status=status, scenario_id=rule.scenario_id, funding_activities=rule.funding_activities, reason=rule.description, evidence_bundle=bundle))
            if "risk" in selection.observation_kinds and rule.risk_theme:
                if milestone_status == "conflict":
                    reason = "触发里程碑存在冲突证据，当前不能确认其状态；以下内容仅作为冲突核验关注点。"
                elif milestone_status == "limited_support":
                    reason = "触发里程碑仅获得有限支持，不能认定该里程碑已经发生；以下内容仅作为后续核验关注点。"
                elif milestone_status == "insufficient_evidence" and requires_milestone:
                    reason = "触发里程碑缺少可验证证据；以下内容仅作为待核验关注点。"
                else:
                    reason = rule.risk_reason or rule.description
                risks.append(FinanceRiskObservation(status=status, risk_theme=rule.risk_theme, reason=reason, missing_information=rule.gap_dimensions, evidence_bundle=bundle))
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
