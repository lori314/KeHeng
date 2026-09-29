"""Strict, source-grounded technology-finance contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowledge.contracts import Citation
from app.knowledge.semantic.contracts import SourceQuality


class StrictFinanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinancialFact(StrictFinanceModel):
    fact_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object_value: str = Field(min_length=1, max_length=2000)
    fact_type: str = Field(min_length=1, max_length=120)
    period: str | None = Field(default=None, max_length=120)
    event_time: datetime | None = None
    quantitative_value: float | None = None
    quantitative_unit: str | None = Field(default=None, max_length=80)
    currency: str | None = Field(default=None, max_length=10)
    source_chunk_id: str = Field(min_length=1)
    citation: Citation
    source_quality: SourceQuality
    financial_dimension: str = Field(min_length=1, max_length=120)
    processor_version: str


class FinancialFactDraft(StrictFinanceModel):
    source_chunk_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object_value: str = Field(min_length=1, max_length=2000)
    fact_type: str = Field(min_length=1, max_length=120)
    financial_dimension: str = Field(min_length=1, max_length=120)
    period: str | None = Field(default=None, max_length=120)
    event_time: datetime | None = None
    quantitative_value: float | None = None
    quantitative_unit: str | None = Field(default=None, max_length=80)
    currency: str | None = Field(default=None, max_length=10)


class FinancialFactExtraction(StrictFinanceModel):
    facts: list[FinancialFactDraft] = Field(default_factory=list, max_length=200)
    information_gaps: list[str] = Field(default_factory=list, max_length=80)


class EvidenceBundle(StrictFinanceModel):
    technology_fact_ids: list[str] = Field(default_factory=list)
    milestone_refs: list[str] = Field(default_factory=list)
    financial_fact_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def needs_source_evidence(self):
        if not (self.technology_fact_ids or self.financial_fact_ids):
            raise ValueError("evidence bundle must reference technology or financial facts")
        return self


class ObservationSelection(StrictFinanceModel):
    rule_id: str
    scenario_id: str | None = None
    technology_fact_ids: list[str] = Field(default_factory=list)
    milestone_refs: list[str] = Field(default_factory=list)
    financial_fact_ids: list[str] = Field(default_factory=list)
    observation_kinds: list[Literal["funding_activity", "risk", "monitoring"]] = Field(min_length=1)


class MappingSelection(StrictFinanceModel):
    selections: list[ObservationSelection] = Field(default_factory=list, max_length=100)


class FinancingActivityObservation(StrictFinanceModel):
    status: Literal["supported", "limited_support", "conflict", "insufficient_evidence"]
    scenario_id: str | None = None
    funding_activities: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1200)
    evidence_bundle: EvidenceBundle


class FinanceRiskObservation(StrictFinanceModel):
    status: Literal["supported", "limited_support", "conflict", "insufficient_evidence"]
    risk_theme: str
    reason: str = Field(min_length=1, max_length=1200)
    missing_information: list[str] = Field(default_factory=list)
    evidence_bundle: EvidenceBundle


class MonitoringNode(StrictFinanceModel):
    node_id: str
    name: str
    reason: str
    related_template: str
    triggering_milestone_ids: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    status: Literal["supported", "limited_support", "conflict", "insufficient_evidence"]
    evidence_bundle: EvidenceBundle


class FinancialInformationGap(StrictFinanceModel):
    dimension_id: str
    description: str
    requested_fields: list[str] = Field(default_factory=list)
    source_rule_ids: list[str] = Field(default_factory=list)


class TechFinanceProfile(StrictFinanceModel):
    profile_id: str
    company_id: str
    company_name: str
    technology_profile_id: str
    technology_stage: str | None = None
    enterprise_lifecycle: Literal["startup", "growth", "mature", "unknown"] = "unknown"
    financial_facts: list[FinancialFact] = Field(default_factory=list)
    financial_scenarios: list[str] = Field(default_factory=list)
    funding_activities: list[FinancingActivityObservation] = Field(default_factory=list)
    risk_observations: list[FinanceRiskObservation] = Field(default_factory=list)
    monitoring_nodes: list[MonitoringNode] = Field(default_factory=list)
    financial_information_gaps: list[FinancialInformationGap] = Field(default_factory=list)
    applicable_rule_ids: list[str] = Field(default_factory=list)
    processor_version: str
    finance_registry_version: str
    created_at: datetime


class ReasoningGuard:
    """Structural output boundary: the public contracts expose no credit decision fields."""

    forbidden_keys = frozenset({"approve", "reject", "credit_limit", "loan_recommendation", "risk_score", "financing_fit_score"})

    @classmethod
    def validate_payload(cls, payload: dict) -> None:
        found = cls.forbidden_keys.intersection(payload)
        if found:
            raise ValueError(f"prohibited finance output fields: {sorted(found)}")
