"""Evidence-bound multi-label domain and composable-template selection."""

from __future__ import annotations

from pathlib import Path

from app.llm import StructuredJSONModel, StructuredModelError
from app.knowledge.contracts import KnowledgeChunk
from app.knowledge.semantic.contracts import (
    ClassifierOutput,
    TechnologyDomainProfile,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.structured_call import complete_contract


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

    async def classify(
        self,
        company_name: str,
        chunks: list[KnowledgeChunk],
        chunk_quality: dict[str, dict[str, str]],
    ) -> tuple[TechnologyDomainProfile, TechnologyTemplateSelection, list[str]]:
        if not chunks:
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
        contexts = [
            {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text[:6000],
                "source_quality": chunk_quality[chunk.chunk_id],
                "citation": chunk.citation.model_dump(mode="json"),
            }
            for chunk in chunks
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
            ClassifierOutput,
            stage="domain/template classifier",
        )
        known_chunks = {chunk.chunk_id for chunk in chunks}
        all_evidence_ids = set(output.domain_evidence_chunk_ids)
        for evidence in output.template_evidence:
            all_evidence_ids.update(evidence.evidence_chunk_ids)
        if not all_evidence_ids.issubset(known_chunks):
            raise StructuredModelError(
                "invalid_evidence_reference",
                "Classifier returned a chunk ID outside its input evidence",
            )
        domain_ids = set(self.registry.domain_by_id)
        if not set(output.primary_domain_ids + output.secondary_domain_ids).issubset(domain_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "Classifier returned an unknown domain ID"
            )
        if set(output.primary_domain_ids) & set(output.secondary_domain_ids):
            raise StructuredModelError(
                "invalid_registry_reference", "A domain cannot be both primary and secondary"
            )
        if not set(output.selected_template_ids).issubset(self.registry.template_by_id):
            raise StructuredModelError(
                "invalid_registry_reference", "Classifier returned an unknown template ID"
            )
        if len(output.selected_template_ids) != len(set(output.selected_template_ids)):
            raise StructuredModelError("invalid_registry_reference", "Duplicate template ID")
        profile = TechnologyDomainProfile(
            status=output.status,
            primary_domains=output.primary_domain_ids,
            secondary_domains=output.secondary_domain_ids,
            evidence_chunk_ids=output.domain_evidence_chunk_ids,
            reason=output.domain_reason,
            registry_version=self.registry.domains.registry_version,
        )
        selection = TechnologyTemplateSelection(
            status=("selected" if output.selected_template_ids else "insufficient_evidence"),
            selected_template_ids=output.selected_template_ids,
            evidence=output.template_evidence,
            reason=output.template_reason,
            registry_version=self.registry.templates.registry_version,
        )
        if output.status == "insufficient_evidence" and output.primary_domain_ids + output.secondary_domain_ids:
            raise StructuredModelError(
                "invalid_registry_reference", "Insufficient domain evidence included labels"
            )
        if output.status == "insufficient_evidence" and output.selected_template_ids:
            raise StructuredModelError(
                "invalid_registry_reference", "Insufficient company evidence selected templates"
            )
        return profile, selection, output.information_gaps


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "prompts" / name
