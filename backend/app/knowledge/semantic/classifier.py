"""Evidence-bound multi-label domain and composable-template selection."""

from __future__ import annotations

import re
from pathlib import Path

from app.llm import StructuredJSONModel, StructuredModelError
from app.knowledge.contracts import KnowledgeChunk
from app.knowledge.semantic.contracts import (
    ClassifierDraft,
    ClassifierReport,
    TemplateEvidence,
    TechnologyDomainProfile,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.structured_call import complete_contract

_SAFE_EVIDENCE_REF = re.compile(r"^E[1-9][0-9]{0,2}$")


class TechnologyDomainClassifier:
    def __init__(
        self,
        model: StructuredJSONModel,
        registry: KnowledgeSemanticRegistry,
        *,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        path = prompt_path or _prompt_path("domain_template_classifier_prompt.md")
        self.prompt = Path(path).read_text(encoding="utf-8")
        self.last_report = ClassifierReport()

    async def classify(
        self,
        company_name: str,
        chunks: list[KnowledgeChunk],
        chunk_quality: dict[str, dict[str, str]],
    ) -> tuple[TechnologyDomainProfile, TechnologyTemplateSelection, list[str]]:
        if not chunks:
            self.last_report = ClassifierReport()
            return (
                TechnologyDomainProfile(
                    status="insufficient_evidence",
                    reason="当前共享知识库没有该企业可用的 GENERAL 原始知识。",
                    registry_version=self.registry.domains.registry_version,
                ),
                TechnologyTemplateSelection(
                    status="insufficient_evidence",
                    reason="没有可供模板选择的企业资料证据。",
                    registry_version=self.registry.templates.registry_version,
                ),
                ["缺少该企业当前 GENERAL 知识"],
            )

        evidence_ref_map = {f"E{index}": chunk for index, chunk in enumerate(chunks, start=1)}
        contexts = [
            {
                "evidence_ref": evidence_ref,
                "text": chunk.text,
                "source_quality": chunk_quality[chunk.chunk_id],
            }
            for evidence_ref, chunk in evidence_ref_map.items()
        ]
        registry = {
            "domains": [item.model_dump(mode="json") for item in self.registry.domains.domains],
            "domain_registry_version": self.registry.domains.registry_version,
            "templates": [item.model_dump(mode="json") for item in self.registry.templates.templates],
            "template_registry_version": self.registry.templates.registry_version,
        }
        output = await complete_contract(
            self.model,
            self.prompt,
            {"company_name": company_name, "general_chunks": contexts, "registry": registry},
            ClassifierDraft,
            stage="domain/template classifier",
        )

        self._validate_registry_ids(output)
        report = ClassifierReport(
            input_evidence_count=len(chunks),
            model_domain_evidence_ref_count=len(output.domain_evidence_refs),
            selected_template_count_before_validation=len(output.selected_template_ids),
        )
        domain_chunk_ids, invalid_domain, duplicates = _map_refs(
            output.domain_evidence_refs, evidence_ref_map
        )
        report.valid_domain_evidence_count = len(domain_chunk_ids)
        report.invalid_domain_evidence_reference_count = len(invalid_domain)
        report.invalid_domain_evidence_refs = _safe_refs(invalid_domain)
        report.duplicate_evidence_reference_count = duplicates
        if invalid_domain:
            report.warnings.append(
                "partial_invalid_domain_evidence_reference"
                if domain_chunk_ids
                else "invalid_domain_evidence_reference"
            )

        domain_status = output.status
        primary_domains = list(output.primary_domain_ids)
        secondary_domains = list(output.secondary_domain_ids)
        domain_reason = output.domain_reason
        if domain_status == "classified" and not domain_chunk_ids:
            domain_status = "insufficient_evidence"
            primary_domains = []
            secondary_domains = []
            domain_reason = "模型返回的领域证据引用无法验证，当前不保留领域分类。"
            report.downgraded_domain_classification = True

        template_evidence_by_id = {item.template_id: item for item in output.template_evidence}
        selected_templates: list[str] = []
        template_evidence: list[TemplateEvidence] = []
        template_invalid_refs: list[str] = []
        for template_id in output.selected_template_ids:
            draft_evidence = template_evidence_by_id.get(template_id)
            if draft_evidence is None:
                report.dropped_template_count += 1
                report.warnings.append("missing_template_evidence_reference")
                continue
            mapped_ids, invalid_refs, duplicate_count = _map_refs(
                draft_evidence.evidence_refs, evidence_ref_map
            )
            report.duplicate_evidence_reference_count += duplicate_count
            report.invalid_template_evidence_reference_count += len(invalid_refs)
            template_invalid_refs.extend(invalid_refs)
            if invalid_refs:
                report.warnings.append(
                    "partial_invalid_template_evidence_reference"
                    if mapped_ids
                    else "invalid_template_evidence_reference"
                )
            if not mapped_ids:
                report.dropped_template_count += 1
                continue
            selected_templates.append(template_id)
            template_evidence.append(
                TemplateEvidence(
                    template_id=template_id,
                    evidence_chunk_ids=mapped_ids,
                    reason=draft_evidence.reason,
                )
            )

        report.invalid_template_evidence_refs = _safe_refs(template_invalid_refs)
        report.selected_template_count_after_validation = len(selected_templates)
        template_reason = output.template_reason
        if output.selected_template_ids and not selected_templates:
            template_reason = "模型返回的模板证据引用无法验证，当前不保留相关模板。"
        self.last_report = report

        profile = TechnologyDomainProfile(
            status=domain_status,
            primary_domains=primary_domains,
            secondary_domains=secondary_domains,
            evidence_chunk_ids=domain_chunk_ids,
            reason=domain_reason,
            registry_version=self.registry.domains.registry_version,
        )
        selection = TechnologyTemplateSelection(
            status=("selected" if selected_templates else "insufficient_evidence"),
            selected_template_ids=selected_templates,
            evidence=template_evidence,
            reason=template_reason,
            registry_version=self.registry.templates.registry_version,
        )
        return profile, selection, output.information_gaps

    def _validate_registry_ids(self, output: ClassifierDraft) -> None:
        domain_ids = set(self.registry.domain_by_id)
        if not set(output.primary_domain_ids + output.secondary_domain_ids).issubset(domain_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "Classifier returned an unknown domain ID"
            )
        if set(output.primary_domain_ids) & set(output.secondary_domain_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "A domain cannot be both primary and secondary"
            )
        template_ids = set(self.registry.template_by_id)
        if not set(output.selected_template_ids).issubset(template_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "Classifier returned an unknown template ID"
            )
        if len(output.selected_template_ids) != len(set(output.selected_template_ids)):
            raise StructuredModelError("invalid_registry_reference", "Duplicate template ID")
        evidence_template_ids = [item.template_id for item in output.template_evidence]
        if len(evidence_template_ids) != len(set(evidence_template_ids)):
            raise StructuredModelError("invalid_registry_reference", "Duplicate template evidence ID")
        if not set(evidence_template_ids).issubset(template_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "Classifier returned an unknown template evidence ID"
            )
        if not set(evidence_template_ids).issubset(set(output.selected_template_ids)):
            raise StructuredModelError(
                "invalid_registry_reference", "Template evidence references an unselected template"
            )
        if output.status == "insufficient_evidence" and (
            output.primary_domain_ids or output.secondary_domain_ids
        ):
            raise StructuredModelError(
                "invalid_registry_reference", "Insufficient domain evidence included labels"
            )
        if output.status == "insufficient_evidence" and output.selected_template_ids:
            raise StructuredModelError(
                "invalid_registry_reference", "Insufficient company evidence selected templates"
            )


def _map_refs(
    evidence_refs: list[str], evidence_ref_map: dict[str, KnowledgeChunk]
) -> tuple[list[str], list[str], int]:
    chunk_ids: list[str] = []
    invalid_refs: list[str] = []
    seen: set[str] = set()
    duplicate_count = 0
    for evidence_ref in evidence_refs:
        if evidence_ref in seen:
            duplicate_count += 1
            continue
        seen.add(evidence_ref)
        chunk = evidence_ref_map.get(evidence_ref)
        if chunk is None:
            invalid_refs.append(evidence_ref)
        else:
            chunk_ids.append(chunk.chunk_id)
    return chunk_ids, invalid_refs, duplicate_count


def _safe_refs(evidence_refs: list[str]) -> list[str]:
    return list(dict.fromkeys(ref if _SAFE_EVIDENCE_REF.fullmatch(ref) else "invalid_ref" for ref in evidence_refs))


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "prompts" / name
