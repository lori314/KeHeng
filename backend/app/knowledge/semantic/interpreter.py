"""Interpret milestone observations with deterministic forbidden-inference guards."""

from __future__ import annotations

from pathlib import Path

from app.llm import StructuredJSONModel, StructuredModelError
from app.knowledge.semantic.contracts import (
    InterpreterOutput,
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

    async def interpret(
        self,
        selection: TechnologyTemplateSelection,
        facts: list[TechnologyFact],
    ) -> tuple[list[MilestoneObservation], list[str]]:
        if not selection.selected_template_ids:
            return [], []
        templates = [
            self.registry.template_by_id[item].model_dump(mode="json")
            for item in selection.selected_template_ids
        ]
        output = await complete_contract(
            self.model,
            self.prompt,
            {
                "selected_templates": templates,
                "technology_facts": [fact.model_dump(mode="json") for fact in facts],
                "standard_reference_metadata_only": [
                    item.model_dump(mode="json")
                    for item in self.registry.standards.references
                ],
            },
            InterpreterOutput,
            stage="technology interpreter",
        )
        fact_by_id = {fact.fact_id: fact for fact in facts}
        selected_templates = set(selection.selected_template_ids)
        observations: dict[tuple[str, str], MilestoneObservation] = {}
        warnings: list[str] = []
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
            cited_ids = set(item.supporting_fact_ids + item.contradicting_fact_ids)
            if not cited_ids.issubset(fact_by_id):
                raise StructuredModelError(
                    "invalid_fact_reference", "Interpreter returned a fact ID outside its input"
                )
            key = (item.template_id, item.milestone_id)
            if key in observations:
                raise StructuredModelError(
                    "invalid_registry_reference", "Interpreter returned duplicate milestone observations"
                )

            relevant_facts = [fact_by_id[fact_id] for fact_id in cited_ids]
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
            if status == "supported" and not item.supporting_fact_ids:
                status = "limited_support"
                reason = "模型未提供可校验的支持 fact ID，不能将该里程碑记为已支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: supported without facts")
            if status == "conflict" and not (
                item.supporting_fact_ids and item.contradicting_fact_ids
            ):
                status = "limited_support"
                reason = "模型未提供成对的支持与反证 fact ID，冲突状态降为有限支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: conflict without both sides")
            if relevant_facts and all(
                fact.source_quality.category in {"snippet_only", "weak_web"}
                for fact in relevant_facts
            ) and status == "supported":
                status = "limited_support"
                reason = "所引事实仅来自低等级网页或搜索摘要，暂记为有限支持。"
                warnings.append(f"{item.template_id}/{item.milestone_id}: weak source quality")
            if offending_markers:
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
                supporting_fact_ids=item.supporting_fact_ids,
                contradicting_fact_ids=item.contradicting_fact_ids,
                reason=reason,
                blocked_inferences=list(dict.fromkeys(blocked)),
            )
            observations[key] = observation

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
        return list(observations.values()), list(dict.fromkeys(output.information_gaps + warnings))


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
