"""Interpret milestone observations with deterministic forbidden-inference guards."""

from __future__ import annotations

from pathlib import Path

from app.llm import StructuredJSONModel, StructuredModelError
from app.knowledge.semantic.contracts import (
    InterpreterOutput,
    InterpreterReport,
    MilestoneObservation,
    TechnologyFact,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.structured_call import complete_contract


class TechnologyInterpreter:
    def __init__(
        self,
        model: StructuredJSONModel,
        registry: KnowledgeSemanticRegistry,
        *,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        path = prompt_path or _prompt_path("technology_interpreter_prompt.md")
        self.prompt = Path(path).read_text(encoding="utf-8")
        self.last_report = InterpreterReport()

    async def interpret(
        self,
        selection: TechnologyTemplateSelection,
        facts: list[TechnologyFact],
    ) -> tuple[list[MilestoneObservation], list[str]]:
        self.last_report = InterpreterReport(input_fact_count=len(facts))
        if not selection.selected_template_ids:
            return [], []
        fact_ref_map = {f"F{index}": fact for index, fact in enumerate(facts, start=1)}
        templates = [
            self.registry.template_by_id[item].model_dump(mode="json")
            for item in selection.selected_template_ids
        ]
        output = await complete_contract(
            self.model,
            self.prompt,
            {
                "selected_templates": templates,
                "technology_facts": [
                    {
                        "fact_ref": fact_ref,
                        "subject": fact.subject,
                        "predicate": fact.predicate,
                        "object_value": fact.object_value,
                        "fact_type": fact.fact_type,
                        "event_time": fact.event_time.isoformat() if fact.event_time else None,
                        "quantitative_value": fact.quantitative_value,
                        "quantitative_unit": fact.quantitative_unit,
                        "domain_tags": fact.domain_tags,
                        "template_tags": fact.template_tags,
                        "source_quality": fact.source_quality.category,
                    }
                    for fact_ref, fact in fact_ref_map.items()
                ],
            },
            InterpreterOutput,
            stage="technology interpreter",
        )
        selected_templates = set(selection.selected_template_ids)
        observations: dict[tuple[str, str], MilestoneObservation] = {}
        warnings: list[str] = []
        invalid_fact_reference_count = 0
        partially_invalid_observation_count = 0
        fully_invalid_observation_count = 0
        duplicate_fact_reference_count = 0
        overlapping_reference_count = 0
        downgraded_observation_count = 0
        for item in output.observations:
            if item.template_id not in selected_templates:
                raise StructuredModelError(
                    "invalid_registry_reference", "Interpreter returned an unselected template"
                )
            template = self.registry.template_by_id[item.template_id]
            milestone_ids = {milestone.id for milestone in template.milestones}
            if item.milestone_id not in milestone_ids:
                raise StructuredModelError(
                    "invalid_registry_reference", "Interpreter returned an unknown milestone"
                )
            key = (item.template_id, item.milestone_id)
            if key in observations:
                raise StructuredModelError(
                    "invalid_registry_reference", "Interpreter returned duplicate milestone observations"
                )

            supporting_refs = _stable_unique(item.supporting_fact_refs)
            contradicting_refs = _stable_unique(item.contradicting_fact_refs)
            duplicate_fact_reference_count += (
                len(item.supporting_fact_refs) - len(supporting_refs)
                + len(item.contradicting_fact_refs) - len(contradicting_refs)
            )
            overlap = set(supporting_refs) & set(contradicting_refs)
            overlapping_reference_count += len(overlap)
            if (
                len(item.supporting_fact_refs) != len(supporting_refs)
                or len(item.contradicting_fact_refs) != len(contradicting_refs)
            ):
                warnings.append(f"duplicate_fact_reference:{item.template_id}/{item.milestone_id}")
            if overlap:
                warnings.append(f"overlapping_fact_reference:{item.template_id}/{item.milestone_id}")

            invalid_supporting_refs = [ref for ref in supporting_refs if ref not in fact_ref_map]
            invalid_contradicting_refs = [ref for ref in contradicting_refs if ref not in fact_ref_map]
            invalid_count = len(invalid_supporting_refs) + len(invalid_contradicting_refs)
            invalid_fact_reference_count += invalid_count
            if invalid_count:
                warnings.append(f"invalid_fact_reference:{item.template_id}/{item.milestone_id}")

            valid_supporting_refs = [
                ref for ref in supporting_refs
                if ref in fact_ref_map and ref not in overlap
            ]
            valid_contradicting_refs = [
                ref for ref in contradicting_refs
                if ref in fact_ref_map and ref not in overlap
            ]
            has_valid_reference = any(
                ref in fact_ref_map
                for ref in supporting_refs + contradicting_refs
            )
            if invalid_count:
                if has_valid_reference:
                    partially_invalid_observation_count += 1
                else:
                    fully_invalid_observation_count += 1

            supporting_facts = [fact_ref_map[ref] for ref in valid_supporting_refs]
            contradicting_facts = [fact_ref_map[ref] for ref in valid_contradicting_refs]
            if item.status == "no_evidence":
                supporting_facts = []
                contradicting_facts = []
            relevant_facts = supporting_facts + contradicting_facts
            template_fact_types = {
                fact.fact_type.casefold()
                for fact in facts
                if item.template_id in fact.template_tags
                or not fact.template_tags
            }
            hinted_milestones = {
                milestone.id
                for milestone in template.milestones
                if template_fact_types.intersection(
                    hint.casefold() for hint in milestone.fact_type_hints
                )
            }
            active_rules = [
                rule
                for rule in template.inference_rules
                if any(
                    fact.fact_type.casefold() in {item.casefold() for item in rule.fact_type_any}
                    for fact in relevant_facts
                )
                or (
                    item.milestone_id in hinted_milestones
                    and template_fact_types.intersection(
                        hint.casefold() for hint in rule.fact_type_any
                    )
                )
            ]
            blocked: list[str] = []
            offending_markers: list[str] = []
            reason_folded = item.reason.casefold()
            marker_rules = []
            for rule in template.inference_rules:
                matching_markers = [
                    marker
                    for marker in rule.blocked_claim_markers
                    if marker.casefold() in reason_folded
                    and not _claim_is_explicitly_negated(item.reason, marker)
                ]
                if matching_markers:
                    marker_rules.append(rule)
                    offending_markers.extend(matching_markers)
            for rule in active_rules:
                blocked.extend(rule.blocked_inferences)
            for rule in marker_rules:
                blocked.extend(rule.blocked_inferences)

            status = item.status
            reason = item.reason
            if status == "no_evidence":
                if item.supporting_fact_refs or item.contradicting_fact_refs:
                    reason = "当前里程碑没有可验证的支持事实。"
                    if invalid_count or overlap:
                        reason = "模型返回的事实引用无法验证，当前里程碑记为无可验证证据。"
                    supporting_facts = []
                    contradicting_facts = []
            elif overlap:
                if not (supporting_facts or contradicting_facts):
                    status = "no_evidence"
                    reason = "模型返回的事实引用无法验证，当前里程碑记为无可验证证据。"
                else:
                    status = "limited_support"
                    reason = "支持与反证包含重叠事实引用，已移除重叠引用并降为有限支持。"
            elif status == "supported" and invalid_count and not supporting_facts:
                status = "no_evidence"
                supporting_facts = []
                contradicting_facts = []
                reason = "模型返回的支持事实引用无法验证，当前里程碑记为无可验证证据。"
            elif status == "supported" and invalid_count:
                status = "limited_support"
                reason = "部分模型事实引用无法验证，仅保留可验证事实，状态降为有限支持。"
            elif status == "conflict" and invalid_count:
                if supporting_facts and contradicting_facts:
                    pass
                elif supporting_facts or contradicting_facts:
                    status = "limited_support"
                    reason = "部分模型事实引用无法验证，仅保留可验证事实，状态降为有限支持。"
                else:
                    status = "no_evidence"
                    reason = "模型返回的事实引用无法验证，当前里程碑记为无可验证证据。"
            elif status == "conflict" and not (supporting_facts or contradicting_facts):
                status = "no_evidence"
                reason = "当前里程碑没有可验证的支持或反证事实。"
            if status == "supported" and not supporting_facts:
                status = "limited_support"
                reason = "模型未提供可校验的支持事实引用，不能将该里程碑记为已支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: supported without facts")
            if status == "conflict" and not (supporting_facts and contradicting_facts):
                status = "limited_support"
                reason = "模型未提供成对的支持与反证事实引用，冲突状态降为有限支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: conflict without both sides")
            if relevant_facts and all(
                fact.source_quality.category in {"snippet_only", "weak_web"}
                for fact in relevant_facts
            ) and status == "supported":
                status = "limited_support"
                reason = "所引事实仅来自低等级网页或搜索摘要，暂记为有限支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: weak source quality")
            if offending_markers:
                if status == "no_evidence":
                    reason = "该里程碑无可验证证据；模型理由中的越界推断已过滤。"
                else:
                    status = "limited_support"
                    allowed = active_rules[0].allowed_interpretation if active_rules else "仅保留原子事实本身"
                    reason = (
                        f"模型输出包含超出所引事实的推断，已按规则过滤；{allowed}。"
                    )
                warnings.append(f"{item.template_id}/{item.milestone_id}: forbidden inference filtered")
                if not blocked:
                    blocked.append("当前引用事实不能支持模型生成的更强结论")
            observation = MilestoneObservation(
                milestone_id=item.milestone_id,
                template_id=item.template_id,
                status=status,
                supporting_fact_ids=[fact.fact_id for fact in supporting_facts],
                contradicting_fact_ids=[fact.fact_id for fact in contradicting_facts],
                reason=reason,
                blocked_inferences=list(dict.fromkeys(blocked)),
            )
            observations[key] = observation
            if status != item.status:
                downgraded_observation_count += 1

        for template_id in selection.selected_template_ids:
            template = self.registry.template_by_id[template_id]
            for milestone in template.milestones:
                key = (template_id, milestone.id)
                if key not in observations:
                    observations[key] = MilestoneObservation(
                        milestone_id=milestone.id,
                        template_id=template_id,
                        status="no_evidence",
                        reason="当前输入的原子科技事实中没有支持该里程碑的证据。",
                    )
        output_observations = list(observations.values())
        self.last_report = InterpreterReport(
            input_fact_count=len(facts),
            model_observation_count=len(output.observations),
            output_observation_count=len(output_observations),
            invalid_fact_reference_count=invalid_fact_reference_count,
            partially_invalid_observation_count=partially_invalid_observation_count,
            fully_invalid_observation_count=fully_invalid_observation_count,
            duplicate_fact_reference_count=duplicate_fact_reference_count,
            overlapping_reference_count=overlapping_reference_count,
            downgraded_observation_count=downgraded_observation_count,
        )
        return output_observations, list(dict.fromkeys(output.information_gaps + warnings))


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "prompts" / name


def _claim_is_explicitly_negated(reason: str, marker: str) -> bool:
    folded = reason.casefold()
    position = folded.find(marker.casefold())
    if position < 0:
        return False
    prefix = folded[max(0, position - 12) : position]
    return any(
        phrase in prefix
        for phrase in ("不能证明", "不能推出", "不能认定", "无法证明", "无法推出", "不支持", "尚不能")
    )


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
