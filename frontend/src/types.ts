export interface ReportCitation {
  citation_id: string; source_id: string; source_type: string; source_title: string | null;
  source_url: string | null; chunk_id: string; excerpt: string | null; page_number: number | null;
  paragraph_number: number | null; locator_text: string | null; source_quality: string;
}
export interface ReportFact {
  fact_id: string; fact_type: string; subject: string; predicate: string; object_value: string;
  period: string | null; event_time: string | null; quantitative_value: number | null;
  quantitative_unit: string | null; source_quality: string; evidence: ReportCitation;
}
export interface CompanyOverviewSection {
  canonical_name: string; aliases: string[]; resolution_status: string; official_website: string | null;
  primary_domain_ids: string[]; primary_domain_names: string[]; selected_template_ids: string[];
  selected_template_names: string[];
}
export interface TechnologyProfileSection {
  fact_count: number; fact_type_distribution: Record<string, number>;
  strongest_evidence_quality_distribution: Record<string, number>; representative_facts: ReportFact[]; all_fact_ids: string[];
}
export interface TechnologyMilestoneSection {
  template_id: string; template_name: string; milestone_id: string; milestone_name: string;
  status: "supported" | "limited_support" | "conflict" | "no_evidence"; status_label: string; reason: string;
  supporting_facts: ReportFact[]; contradicting_facts: ReportFact[]; blocked_inferences: string[];
}
export interface ReportAssertion {
  assertion_id: string; assertion_domain: string; assertion_type: string; evidence_status: string;
  grouping_method: string; member_fact_count: number; supporting_source_count: number; strongest_source_quality: string;
  representative_fact: ReportFact; member_fact_ids: string[];
}
export interface EvidenceAssertionSummarySection {
  atomic_fact_count: number; assertion_count: number; technology_assertion_count: number;
  financial_assertion_count: number; single_source_count: number; multi_source_support_count: number;
  conflict_count: number; compression_ratio: number; representative_assertions: ReportAssertion[];
}
export interface FinancialDimensionSection { dimension_id: string; dimension_name: string; fact_count: number; representative_facts: ReportFact[] }
export interface FinancialProfileSection {
  fact_count: number; dimension_distribution: Record<string, number>; source_quality_distribution: Record<string, number>;
  dimensions: FinancialDimensionSection[]; all_fact_ids: string[];
}
export interface MilestoneReference { template_id: string; milestone_id: string; milestone_name: string; status: string; status_label: string }
export interface ReportEvidenceBundle { technology_facts: ReportFact[]; milestones: MilestoneReference[]; financial_facts: ReportFact[]; rule_ids: string[] }
export interface TechnologyFinanceRuleSection { rule_id: string; rule_title: string; scenario_id: string | null; scenario_name: string | null }
export interface FundingActivitySection { status: string; status_label: string; scenario_id: string | null; activities: string[]; reason: string; evidence: ReportEvidenceBundle }
export interface RiskSection { status: string; status_label: string; risk_theme: string; reason: string; missing_information: string[]; evidence: ReportEvidenceBundle }
export interface MonitoringNodeSection {
  node_id: string; name: string; status: string; status_label: string; status_explanation: string; reason: string;
  required_evidence_types: string[]; triggering_milestones: string[]; evidence: ReportEvidenceBundle;
}
export interface TechnologyFinanceSection {
  applicable_rules: TechnologyFinanceRuleSection[]; funding_activities: FundingActivitySection[];
  risks: RiskSection[]; monitoring_nodes: MonitoringNodeSection[];
}
export interface InformationGapItem { dimension_id: string; dimension_name: string; description: string; requested_fields: string[]; source_rule_ids: string[] }
export interface InformationGapSection {
  total_gap_count: number; deduplicated_financial_dimension_count: number; financial_gaps: InformationGapItem[];
  semantic_gaps: string[]; all_semantic_gaps: string[];
}
export interface SourceSummarySection {
  evidence_source_count: number; source_type_distribution: Record<string, number>; source_quality_distribution: Record<string, number>;
  first_party_count: number; authoritative_public_record_count: number; weak_web_count: number;
  snippet_only_count: number; official_website_verified: boolean;
}
export interface MethodologySection { positioning: string; evidence_boundary: string[]; processor_versions: Record<string, string>; registry_versions: Record<string, string>; warnings: string[] }
export interface EvidenceFirstReport {
  report_id: string; company_id: string; company_name: string; generated_at: string; report_version: string;
  company_overview: CompanyOverviewSection; technology_profile: TechnologyProfileSection;
  technology_milestones: TechnologyMilestoneSection[]; evidence_assertions: EvidenceAssertionSummarySection;
  financial_profile: FinancialProfileSection; technology_finance_links: TechnologyFinanceSection;
  information_gaps: InformationGapSection; source_summary: SourceSummarySection; methodology: MethodologySection;
}
export type EvidenceStage = "queued" | "research" | "technology_semantic" | "technology_finance" | "evidence_assertions" | "evidence_first_report" | "complete" | "failed";
export interface EvidenceAnalysisCreateResponse { task_id: string; status: "processing" }
export interface EvidenceAnalysisTaskResponse {
  task_id: string; status: "processing" | "completed" | "failed"; current_stage: EvidenceStage; enterprise_name: string;
  result_status: "completed" | "entity_not_resolved" | null; company_id: string | null; canonical_name: string | null;
  technology_profile_id: string | null; finance_profile_id: string | null; assertion_profile_id: string | null;
  report: EvidenceFirstReport | null; report_url: string | null;
  error: { code: string; category: string | null; stage: string; message: string } | null;
}
