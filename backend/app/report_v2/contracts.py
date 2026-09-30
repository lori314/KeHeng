"""Public JSON contracts for deterministic V2 evidence reports."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportCitation(StrictModel):
    citation_id: str
    source_id: str
    source_type: str
    source_title: str | None = None
    source_url: str | None = None
    chunk_id: str
    excerpt: str | None = None
    page_number: int | None = None
    paragraph_number: int | None = None
    locator_text: str | None = None
    source_quality: str


class ReportFact(StrictModel):
    fact_id: str
    fact_type: str
    subject: str
    predicate: str
    object_value: str
    period: str | None = None
    event_time: datetime | None = None
    quantitative_value: float | None = None
    quantitative_unit: str | None = None
    source_quality: str
    evidence: ReportCitation


class CompanyOverviewSection(StrictModel):
    canonical_name: str
    aliases: list[str]
    resolution_status: str
    official_website: str | None
    primary_domain_ids: list[str]
    primary_domain_names: list[str]
    selected_template_ids: list[str]
    selected_template_names: list[str]


class TechnologyProfileSection(StrictModel):
    fact_count: int
    fact_type_distribution: dict[str, int]
    strongest_evidence_quality_distribution: dict[str, int]
    representative_facts: list[ReportFact]
    all_fact_ids: list[str]


class TechnologyMilestoneSection(StrictModel):
    template_id: str
    template_name: str
    milestone_id: str
    milestone_name: str
    status: Literal["supported", "limited_support", "conflict", "no_evidence"]
    status_label: str
    reason: str
    supporting_facts: list[ReportFact]
    contradicting_facts: list[ReportFact]
    blocked_inferences: list[str]


class ReportAssertion(StrictModel):
    assertion_id: str
    assertion_domain: str
    assertion_type: str
    evidence_status: str
    grouping_method: str
    member_fact_count: int
    supporting_source_count: int
    strongest_source_quality: str
    representative_fact: ReportFact
    member_fact_ids: list[str]


class EvidenceAssertionSummarySection(StrictModel):
    atomic_fact_count: int
    assertion_count: int
    technology_assertion_count: int
    financial_assertion_count: int
    single_source_count: int
    multi_source_support_count: int
    conflict_count: int
    compression_ratio: float
    representative_assertions: list[ReportAssertion]


class FinancialDimensionSection(StrictModel):
    dimension_id: str
    dimension_name: str
    fact_count: int
    representative_facts: list[ReportFact]


class FinancialProfileSection(StrictModel):
    fact_count: int
    dimension_distribution: dict[str, int]
    source_quality_distribution: dict[str, int]
    dimensions: list[FinancialDimensionSection]
    all_fact_ids: list[str]


class MilestoneReference(StrictModel):
    template_id: str
    milestone_id: str
    milestone_name: str
    status: str
    status_label: str


class ReportEvidenceBundle(StrictModel):
    technology_facts: list[ReportFact]
    milestones: list[MilestoneReference]
    financial_facts: list[ReportFact]
    rule_ids: list[str]


class TechnologyFinanceRuleSection(StrictModel):
    rule_id: str
    rule_title: str
    scenario_id: str | None
    scenario_name: str | None


class FundingActivitySection(StrictModel):
    status: str
    status_label: str
    scenario_id: str | None
    activities: list[str]
    reason: str
    evidence: ReportEvidenceBundle


class RiskSection(StrictModel):
    status: str
    status_label: str
    risk_theme: str
    reason: str
    missing_information: list[str]
    evidence: ReportEvidenceBundle


class MonitoringNodeSection(StrictModel):
    node_id: str
    name: str
    status: str
    status_label: str
    status_explanation: str
    reason: str
    required_evidence_types: list[str]
    triggering_milestones: list[str]
    evidence: ReportEvidenceBundle


class TechnologyFinanceSection(StrictModel):
    applicable_rules: list[TechnologyFinanceRuleSection]
    funding_activities: list[FundingActivitySection]
    risks: list[RiskSection]
    monitoring_nodes: list[MonitoringNodeSection]


class InformationGapItem(StrictModel):
    dimension_id: str
    dimension_name: str
    description: str
    requested_fields: list[str]
    source_rule_ids: list[str]


class InformationGapSection(StrictModel):
    total_gap_count: int
    deduplicated_financial_dimension_count: int
    financial_gaps: list[InformationGapItem]
    semantic_gaps: list[str]
    all_semantic_gaps: list[str]


class SourceSummarySection(StrictModel):
    evidence_source_count: int
    source_type_distribution: dict[str, int]
    source_quality_distribution: dict[str, int]
    first_party_count: int
    authoritative_public_record_count: int
    weak_web_count: int
    snippet_only_count: int
    official_website_verified: bool


class MethodologySection(StrictModel):
    positioning: str
    evidence_boundary: list[str]
    processor_versions: dict[str, str]
    registry_versions: dict[str, str]
    warnings: list[str]


class EvidenceFirstReport(StrictModel):
    report_id: str
    company_id: str
    company_name: str
    generated_at: datetime
    report_version: str
    company_overview: CompanyOverviewSection
    technology_profile: TechnologyProfileSection
    technology_milestones: list[TechnologyMilestoneSection]
    evidence_assertions: EvidenceAssertionSummarySection
    financial_profile: FinancialProfileSection
    technology_finance_links: TechnologyFinanceSection
    information_gaps: InformationGapSection
    source_summary: SourceSummarySection
    methodology: MethodologySection


class ReportErrorDetail(StrictModel):
    code: str
    message: str
    stage: str | None = None
