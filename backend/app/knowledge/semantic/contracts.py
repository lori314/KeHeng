"""Versioned data contracts for source-grounded technology semantics."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowledge.contracts import Citation


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TechnologyDomainProfile(StrictModel):
    status: Literal["classified", "insufficient_evidence"]
    primary_domains: list[str] = Field(default_factory=list)
    secondary_domains: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1200)
    registry_version: str

    @model_validator(mode="after")
    def require_evidence_for_classification(self):
        if self.status == "classified" and not self.evidence_chunk_ids:
            raise ValueError("classified domains require evidence chunk IDs")
        if self.status == "classified" and not (
            self.primary_domains or self.secondary_domains
        ):
            raise ValueError("classified status requires at least one domain")
        if self.status == "insufficient_evidence" and (
            self.primary_domains or self.secondary_domains
        ):
            raise ValueError("insufficient evidence cannot carry domain labels")
        if len(set(self.primary_domains)) != len(self.primary_domains):
            raise ValueError("primary domain IDs must be unique")
        if len(set(self.secondary_domains)) != len(self.secondary_domains):
            raise ValueError("secondary domain IDs must be unique")
        if set(self.primary_domains) & set(self.secondary_domains):
            raise ValueError("primary and secondary domains cannot overlap")
        return self


class TemplateEvidence(StrictModel):
    template_id: str
    evidence_chunk_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=800)


class TechnologyTemplateSelection(StrictModel):
    status: Literal["selected", "insufficient_evidence"]
    selected_template_ids: list[str] = Field(default_factory=list)
    evidence: list[TemplateEvidence] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1200)
    registry_version: str

    @model_validator(mode="after")
    def selection_and_evidence_must_match(self):
        evidence_ids = [item.template_id for item in self.evidence]
        if len(set(self.selected_template_ids)) != len(self.selected_template_ids):
            raise ValueError("template IDs must be unique")
        if set(evidence_ids) != set(self.selected_template_ids):
            raise ValueError("every selected template must have evidence and vice versa")
        if self.status == "insufficient_evidence" and self.selected_template_ids:
            raise ValueError("insufficient evidence cannot carry template selections")
        if self.status == "selected" and not self.selected_template_ids:
            raise ValueError("selected status requires at least one template")
        return self


class SourceQuality(StrictModel):
    category: Literal[
        "authoritative_public_record",
        "first_party",
        "academic_or_patent",
        "third_party",
        "weak_web",
        "snippet_only",
    ]
    source_type: str
    content_scope: str
    rationale: str


class SemanticEvidenceSelectionReport(StrictModel):
    available_chunk_count: int = Field(default=0, ge=0)
    selected_chunk_count: int = Field(default=0, ge=0)
    selected_chunk_ids: list[str] = Field(default_factory=list)
    selected_source_count: int = Field(default=0, ge=0)
    selected_char_count: int = Field(default=0, ge=0)
    truncated_chunk_count: int = Field(default=0, ge=0)
    quality_distribution: dict[str, int] = Field(default_factory=dict)
    content_scope_distribution: dict[str, int] = Field(default_factory=dict)
    source_type_distribution: dict[str, int] = Field(default_factory=dict)
    dropped_due_to_budget: int = Field(default=0, ge=0)
    dropped_due_to_source_cap: int = Field(default=0, ge=0)
    dropped_as_duplicate: int = Field(default=0, ge=0)


class FactExtractionReport(StrictModel):
    batch_count: int = Field(default=0, ge=0)
    successful_batch_count: int = Field(default=0, ge=0)
    failed_batch_count: int = Field(default=0, ge=0)
    input_chunk_count: int = Field(default=0, ge=0)
    successful_chunk_count: int = Field(default=0, ge=0)
    failed_chunk_count: int = Field(default=0, ge=0)
    fact_count: int = Field(default=0, ge=0)
    batch_size: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=0, ge=0)
    error_categories: dict[str, int] = Field(default_factory=dict)
    rejected_evidence_reference_count: int = Field(default=0, ge=0)
    rejected_tag_count: int = Field(default=0, ge=0)
    rejected_fact_type_count: int = Field(default=0, ge=0)
    rejected_fact_type_distribution: dict[str, int] = Field(default_factory=dict)


class TechnologyFact(StrictModel):
    fact_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object_value: str = Field(min_length=1, max_length=2000)
    fact_type: str = Field(min_length=1, max_length=120)
    event_time: datetime | None = None
    quantitative_value: float | None = None
    quantitative_unit: str | None = None
    source_chunk_id: str = Field(min_length=1)
    citation: Citation
    domain_tags: list[str] = Field(default_factory=list)
    template_tags: list[str] = Field(default_factory=list)
    source_quality: SourceQuality
    processor_version: str


class MilestoneObservation(StrictModel):
    milestone_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    status: Literal["supported", "limited_support", "conflict", "no_evidence"]
    supporting_fact_ids: list[str] = Field(default_factory=list)
    contradicting_fact_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1200)
    blocked_inferences: list[str] = Field(default_factory=list)


class TechnologySemanticProfile(StrictModel):
    profile_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    input_general_chunk_ids: list[str] = Field(default_factory=list)
    input_general_chunk_count: int = Field(default=0, ge=0)
    processed_general_chunk_count: int = Field(default=0, ge=0)
    semantic_evidence_selection: SemanticEvidenceSelectionReport = Field(
        default_factory=SemanticEvidenceSelectionReport
    )
    classifier_report: "ClassifierReport" = Field(default_factory=lambda: ClassifierReport())
    fact_extraction_report: FactExtractionReport = Field(default_factory=FactExtractionReport)
    domain_profile: TechnologyDomainProfile
    template_selection: TechnologyTemplateSelection
    technology_facts: list[TechnologyFact] = Field(default_factory=list)
    interpreter_report: "InterpreterReport" = Field(default_factory=lambda: InterpreterReport())
    milestone_observations: list[MilestoneObservation] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    processor_version: str
    domain_registry_version: str
    template_registry_version: str
    standard_registry_version: str
    technology_fact_type_registry_version: str = "unknown"
    created_at: datetime
    processing_warnings: list[str] = Field(default_factory=list)


class TemplateEvidenceDraft(StrictModel):
    template_id: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    reason: str = Field(min_length=1, max_length=800)


class ClassifierDraft(StrictModel):
    status: Literal["classified", "insufficient_evidence"]
    primary_domain_ids: list[str] = Field(default_factory=list, max_length=20)
    secondary_domain_ids: list[str] = Field(default_factory=list, max_length=20)
    domain_evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    domain_reason: str = Field(min_length=1, max_length=1200)
    selected_template_ids: list[str] = Field(default_factory=list, max_length=20)
    template_evidence: list[TemplateEvidenceDraft] = Field(default_factory=list, max_length=20)
    template_reason: str = Field(min_length=1, max_length=1200)
    information_gaps: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def domain_labels_are_consistent(self):
        if self.status == "classified" and not (self.primary_domain_ids or self.secondary_domain_ids):
            raise ValueError("classified status requires at least one domain ID")
        if set(self.primary_domain_ids) & set(self.secondary_domain_ids):
            raise ValueError("primary and secondary domain IDs cannot overlap")
        if len(set(self.primary_domain_ids)) != len(self.primary_domain_ids):
            raise ValueError("primary domain IDs must be unique")
        if len(set(self.secondary_domain_ids)) != len(self.secondary_domain_ids):
            raise ValueError("secondary domain IDs must be unique")
        return self


class ClassifierReport(StrictModel):
    input_evidence_count: int = Field(default=0, ge=0)
    model_domain_evidence_ref_count: int = Field(default=0, ge=0)
    valid_domain_evidence_count: int = Field(default=0, ge=0)
    invalid_domain_evidence_reference_count: int = Field(default=0, ge=0)
    invalid_domain_evidence_refs: list[str] = Field(default_factory=list)
    selected_template_count_before_validation: int = Field(default=0, ge=0)
    selected_template_count_after_validation: int = Field(default=0, ge=0)
    invalid_template_evidence_reference_count: int = Field(default=0, ge=0)
    invalid_template_evidence_refs: list[str] = Field(default_factory=list)
    dropped_template_count: int = Field(default=0, ge=0)
    duplicate_evidence_reference_count: int = Field(default=0, ge=0)
    downgraded_domain_classification: bool = False
    warnings: list[str] = Field(default_factory=list)


class TechnologyFactDraft(StrictModel):
    source_chunk_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=300)
    object_value: str = Field(min_length=1, max_length=2000)
    fact_type: str = Field(min_length=1, max_length=120)
    event_time: datetime | None = None
    quantitative_value: float | None = None
    quantitative_unit: str | None = None
    domain_tags: list[str] = Field(default_factory=list)
    template_tags: list[str] = Field(default_factory=list)


class FactExtractionOutput(StrictModel):
    facts: list[TechnologyFactDraft] = Field(default_factory=list, max_length=200)
    information_gaps: list[str] = Field(default_factory=list, max_length=40)


class FactExtractionBatchOutput(StrictModel):
    facts: list[TechnologyFactDraft] = Field(default_factory=list, max_length=36)
    information_gaps: list[str] = Field(default_factory=list, max_length=12)


class InterpreterObservationDraft(StrictModel):
    milestone_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    status: Literal["supported", "limited_support", "conflict", "no_evidence"]
    supporting_fact_refs: list[str] = Field(default_factory=list)
    contradicting_fact_refs: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1200)


class InterpreterOutput(StrictModel):
    observations: list[InterpreterObservationDraft] = Field(default_factory=list, max_length=100)
    information_gaps: list[str] = Field(default_factory=list, max_length=40)


class InterpreterReport(StrictModel):
    input_fact_count: int = Field(default=0, ge=0)
    model_observation_count: int = Field(default=0, ge=0)
    output_observation_count: int = Field(default=0, ge=0)
    invalid_fact_reference_count: int = Field(default=0, ge=0)
    partially_invalid_observation_count: int = Field(default=0, ge=0)
    fully_invalid_observation_count: int = Field(default=0, ge=0)
    duplicate_fact_reference_count: int = Field(default=0, ge=0)
    overlapping_reference_count: int = Field(default=0, ge=0)
    downgraded_observation_count: int = Field(default=0, ge=0)


class PersistedSemanticResult(StrictModel):
    company_id: str
    processor_version: str
    template_registry_version: str
    profile_json: dict[str, Any]
