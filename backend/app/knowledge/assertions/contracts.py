"""Versioned contracts for deterministic evidence assertion groups."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    company_id: str
    assertion_domain: Literal["technology", "finance"]
    assertion_type: str
    representative_fact_id: str
    member_fact_ids: list[str]
    supporting_source_ids: list[str]
    supporting_source_count: int = Field(ge=1)
    evidence_status: Literal["single_source", "multi_source_support", "conflict"]
    source_quality_distribution: dict[str, int]
    strongest_source_quality: str
    conflict_fact_ids: list[str] = Field(default_factory=list)
    grouping_method: Literal[
        "exact",
        "normalized_quantitative",
        "containment",
        "fuzzy_text",
        "conflict_slot",
    ]
    similarity_score: float | None = Field(default=None, ge=0, le=1)
    normalization_key: str
    processor_version: str


class EvidenceAssertionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    company_id: str
    technology_profile_id: str | None = None
    finance_profile_id: str | None = None
    assertions: list[EvidenceAssertion] = Field(default_factory=list)
    technology_assertion_count: int = Field(ge=0)
    financial_assertion_count: int = Field(ge=0)
    single_source_count: int = Field(ge=0)
    multi_source_support_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    company_subject_normalized_count: int = Field(default=0, ge=0)
    period_normalized_count: int = Field(default=0, ge=0)
    exact_merge_count: int = Field(default=0, ge=0)
    quantitative_merge_count: int = Field(default=0, ge=0)
    containment_merge_count: int = Field(default=0, ge=0)
    fuzzy_merge_count: int = Field(default=0, ge=0)
    numeric_guard_rejection_count: int = Field(default=0, ge=0)
    model_anchor_guard_rejection_count: int = Field(default=0, ge=0)
    candidate_pair_count: int = Field(default=0, ge=0)
    merged_fact_count: int = Field(default=0, ge=0)
    suspicious_fuzzy_cluster_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    processor_version: str
    created_at: datetime
