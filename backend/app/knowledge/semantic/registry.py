"""Load and validate the small, versioned registries used by semantic processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unicodedata

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    selection_terms: list[str] = Field(min_length=1)
    evidence_types: list[str]
    retrieval_hints: list[str]
    milestones: list[MilestoneDefinition]
    inference_rules: list[InferenceRule]

    @model_validator(mode="after")
    def selection_terms_are_stable_and_unique(self):
        normalized = [
            " ".join(unicodedata.normalize("NFKC", term).casefold().split())
            for term in self.selection_terms
        ]
        if any(not term for term in normalized):
            raise ValueError("technology template selection_terms must be non-empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("technology template selection_terms must be unique")
        return self


class TemplateRegistry(RegistryModel):
    registry_id: str
    registry_version: str
    templates: list[TechnologyTemplate]


class TechnologyFactType(RegistryModel):
    id: str
    category: str
    description: str


class TechnologyFactTypeRegistry(RegistryModel):
    registry_id: str
    registry_version: str
    excluded_semantic_categories: list[str] = Field(default_factory=list)
    fact_types: list[TechnologyFactType]


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
        self.fact_types = _load(
            root / "technology_fact_types.yaml", TechnologyFactTypeRegistry
        )
        self.standards = _load(root / "standard_references.yaml", StandardReferenceRegistry)
        _require_unique((item.id for item in self.domains.domains), "domain IDs")
        _require_unique((item.id for item in self.templates.templates), "template IDs")
        _require_unique((item.id for item in self.standards.references), "standard IDs")
        for template in self.templates.templates:
            _require_unique((item.id for item in template.milestones), f"{template.id} milestone IDs")
        _require_unique((item.id.casefold() for item in self.fact_types.fact_types), "technology fact type IDs")
        _require_template_fact_types_registered(self.templates, self.fact_types)

    @property
    def domain_by_id(self) -> dict[str, DomainEntry]:
        return {item.id: item for item in self.domains.domains}

    @property
    def template_by_id(self) -> dict[str, TechnologyTemplate]:
        return {item.id: item for item in self.templates.templates}

    @property
    def allowed_technology_fact_types(self) -> set[str]:
        return {item.id.casefold() for item in self.fact_types.fact_types}

    @property
    def technology_fact_type_by_id(self) -> dict[str, TechnologyFactType]:
        return {item.id.casefold(): item for item in self.fact_types.fact_types}


def _require_template_fact_types_registered(
    templates: TemplateRegistry,
    fact_types: TechnologyFactTypeRegistry,
) -> None:
    registered = {item.id.casefold() for item in fact_types.fact_types}
    referenced: set[str] = set()
    for template in templates.templates:
        referenced.update(item.casefold() for item in template.evidence_types)
        for milestone in template.milestones:
            referenced.update(item.casefold() for item in milestone.fact_type_hints)
        for rule in template.inference_rules:
            referenced.update(item.casefold() for item in rule.fact_type_any)
    missing = sorted(referenced - registered)
    if missing:
        raise ValueError(
            "technology template references unregistered fact types: "
            + ", ".join(missing)
        )


def _load(path: Path, contract):
    with path.open("r", encoding="utf-8") as stream:
        raw: Any = yaml.safe_load(stream)
    return contract.model_validate(raw)


def _require_unique(values, label: str) -> None:
    sequence = list(values)
    if len(sequence) != len(set(sequence)):
        raise ValueError(f"duplicate {label} in knowledge registry")
