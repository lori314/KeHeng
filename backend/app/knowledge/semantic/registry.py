"""Load and validate the small, versioned registries used by semantic processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainEntry(RegistryModel):
    id: str
    name: str


class DomainRegistry(RegistryModel):
    registry_id: str
    registry_version: str
    taxonomy: str
    source: str
    source_url: str
    domains: list[DomainEntry]


class InferenceRule(RegistryModel):
    fact_type_any: list[str] = Field(min_length=1)
    allowed_interpretation: str
    blocked_inferences: list[str]
    blocked_claim_markers: list[str]


class MilestoneDefinition(RegistryModel):
    id: str
    label: str
    fact_type_hints: list[str]


class TechnologyTemplate(RegistryModel):
    id: str
    name: str
    important_objects: list[str]
    evidence_types: list[str]
    retrieval_hints: list[str]
    milestones: list[MilestoneDefinition]
    inference_rules: list[InferenceRule]


class TemplateRegistry(RegistryModel):
    registry_id: str
    registry_version: str
    templates: list[TechnologyTemplate]


class StandardReference(RegistryModel):
    id: str
    number: str
    name: str
    scope: str
    version_status: str
    source_url: str
    use_boundary: str


class StandardReferenceRegistry(RegistryModel):
    registry_id: str
    registry_version: str
    references: list[StandardReference]


class KnowledgeSemanticRegistry:
    def __init__(self, config_root: str | Path | None = None) -> None:
        root = Path(config_root) if config_root else Path(__file__).resolve().parents[4] / "configs" / "knowledge"
        self.domains = _load(root / "domain_registry.yaml", DomainRegistry)
        self.templates = _load(root / "technology_templates.yaml", TemplateRegistry)
        self.standards = _load(root / "standard_references.yaml", StandardReferenceRegistry)
        _require_unique((item.id for item in self.domains.domains), "domain IDs")
        _require_unique((item.id for item in self.templates.templates), "template IDs")
        _require_unique((item.id for item in self.standards.references), "standard IDs")
        for template in self.templates.templates:
            _require_unique((item.id for item in template.milestones), f"{template.id} milestone IDs")

    @property
    def domain_by_id(self) -> dict[str, DomainEntry]:
        return {item.id: item for item in self.domains.domains}

    @property
    def template_by_id(self) -> dict[str, TechnologyTemplate]:
        return {item.id: item for item in self.templates.templates}


def _load(path: Path, contract):
    with path.open("r", encoding="utf-8") as stream:
        raw: Any = yaml.safe_load(stream)
    return contract.model_validate(raw)


def _require_unique(values, label: str) -> None:
    sequence = list(values)
    if len(sequence) != len(set(sequence)):
        raise ValueError(f"duplicate {label} in knowledge registry")
