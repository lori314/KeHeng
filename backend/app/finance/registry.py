"""Versioned finance registries; policy references are metadata, not evidence citations."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RegistryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dimension(RegistryItem):
    id: str
    label: str
    description: str


class Scenario(RegistryItem):
    id: str
    label: str
    subclasses: list[str] = Field(default_factory=list)


class LifecycleService(RegistryItem):
    id: str
    lifecycle: str
    label: str


class FinanceRule(RegistryItem):
    rule_id: str
    category: str
    title: str
    description: str
    applicable_conditions: dict[str, Any]
    output_type: list[str]
    scenario_id: str | None = None
    funding_activities: list[str] = Field(default_factory=list)
    risk_theme: str | None = None
    risk_reason: str | None = None
    monitoring_nodes: list[dict[str, Any]] = Field(default_factory=list)
    gap_dimensions: list[str] = Field(default_factory=list)
    source_document: str
    source_authority: str
    source_date: str
    source_reference: str
    source_url: str
    use_boundary: str
    registry_version: str


class RegistryDescriptor(RegistryItem):
    registry_id: str
    registry_version: str
    section: str


class PolicySourceMetadata(RegistryItem):
    id: str
    source_document: str
    source_authority: str
    source_date: str
    source_reference: str
    source_url: str
    use_boundary: str


class FinanceRegistry:
    def __init__(self, config_root: str | Path | None = None) -> None:
        root = Path(config_root) if config_root else Path(__file__).resolve().parents[3] / "configs" / "finance"
        with (root / "finance_registry.yaml").open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        self.registry_version = data["registry_version"]
        self.registry_catalog = [RegistryDescriptor.model_validate(x) for x in data["registry_catalog"]]
        self.source_metadata = [PolicySourceMetadata.model_validate(x) for x in data["source_metadata"]]
        self.lifecycle_services = [LifecycleService.model_validate(x) for x in data["lifecycle_services"]]
        self.innovation_dimensions = [Dimension.model_validate(x) for x in data["innovation_dimensions"]]
        self.due_diligence_dimensions = [Dimension.model_validate(x) for x in data["due_diligence_dimensions"]]
        self.scenarios = [Scenario.model_validate(x) for x in data["scenarios"]]
        self.rules = [FinanceRule.model_validate(x) for x in data["rules"]]
        self.prohibited_conclusions = tuple(data["prohibited_conclusions"])
        for values, label in ((self.innovation_dimensions, "innovation dimension"), (self.due_diligence_dimensions, "due diligence dimension"), (self.scenarios, "scenario"), (self.rules, "rule")):
            ids = [x.id if hasattr(x, "id") else x.rule_id for x in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} IDs")
        self.dimension_by_id = {x.id: x for x in self.innovation_dimensions + self.due_diligence_dimensions}
        self.scenario_by_id = {x.id: x for x in self.scenarios}
        self.rule_by_id = {x.rule_id: x for x in self.rules}
        required_sections = {"lifecycle_services", "innovation_dimensions", "scenarios", "due_diligence_dimensions"}
        if {x.section for x in self.registry_catalog} != required_sections:
            raise ValueError("finance registry catalog must declare all four registries")
        _unique([x.registry_id for x in self.registry_catalog], "registry catalog IDs")
        _unique([x.id for x in self.source_metadata], "policy source IDs")
        allowed_outputs = {"funding_activity", "risk", "monitoring"}
        for rule in self.rules:
            if rule.scenario_id is not None and rule.scenario_id not in self.scenario_by_id:
                raise ValueError(f"finance rule {rule.rule_id} references unknown scenario")
            if not set(rule.output_type).issubset(allowed_outputs) or not rule.output_type:
                raise ValueError(f"finance rule {rule.rule_id} has invalid output_type")
            if not set(rule.gap_dimensions).issubset(self.dimension_by_id):
                raise ValueError(f"finance rule {rule.rule_id} references unknown gap dimension")
            if not all((rule.source_document, rule.source_authority, rule.source_date, rule.source_reference, rule.source_url, rule.use_boundary, rule.registry_version)):
                raise ValueError(f"finance rule {rule.rule_id} is missing source metadata")


def _unique(ids: list[str], label: str) -> None:
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label}")
