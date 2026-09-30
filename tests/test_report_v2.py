"""Contract tests for deterministic V2 evidence-first reporting."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.finance.contracts import EvidenceBundle, FinancialFact, FinancialInformationGap, FinancingActivityObservation, TechFinanceProfile  # noqa: E402
from app.knowledge.assertions.contracts import EvidenceAssertionProfile  # noqa: E402
from app.knowledge.contracts import Citation, CitationLocator, Company, Source, SourceType  # noqa: E402
from app.knowledge.semantic.contracts import (  # noqa: E402
    MilestoneObservation,
    SourceQuality,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologySemanticProfile,
    TechnologyTemplateSelection,
    TemplateEvidence,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry  # noqa: E402
from app.report_v2 import EvidenceFirstReportAssembler  # noqa: E402


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


class MemoryRepository:
    def __init__(self):
        self.company = Company(company_id="co-1", canonical_name="示例科技", resolution_status="resolved")
        self.sources = {}
        self.tech = None
        self.finance = None
        self.assertions = None

    def get_company(self, company_id):
        return self.company if company_id == self.company.company_id else None

    def get_technology_semantic_profile(self, company_id):
        return self.tech.model_dump(mode="json") if self.tech else None

    def get_technology_finance_profile(self, company_id, *, technology_profile_id=None):
        if self.finance and (technology_profile_id is None or self.finance.technology_profile_id == technology_profile_id):
            return self.finance.model_dump(mode="json")
        return None

    def get_evidence_assertion_profile(self, company_id, *, technology_profile_id=None, finance_profile_id=None):
        if self.assertions and (technology_profile_id is None or self.assertions.technology_profile_id == technology_profile_id) and (finance_profile_id is None or self.assertions.finance_profile_id == finance_profile_id):
            return self.assertions.model_dump(mode="json")
        return None

    def get_source(self, source_id):
        return self.sources.get(source_id)

    def list_current_chunks(self, company_id, knowledge_layer):
        return []


def make_profiles(repo):
    registry = KnowledgeSemanticRegistry()
    template = registry.templates.templates[0]
    milestone = template.milestones[0]
    repo.sources["src-1"] = Source(source_id="src-1", source_type=SourceType.WEB, title="Source", canonical_url="https://example.test")
    citation = Citation(citation_id="cit-1", source_id="src-1", source_title="Source", excerpt="原文节选", locator=CitationLocator(page_number=2, paragraph_number=3))
    tech_fact = TechnologyFact(
        fact_id="t-1", company_id="co-1", subject="产品", predicate="发布", object_value="芯片 A",
        fact_type="product_release", source_chunk_id="chunk-1", citation=citation,
        source_quality=SourceQuality(category="weak_web", source_type="web", content_scope="full_content", rationale="test"),
        processor_version="tech.v1",
    )
    repo.tech = TechnologySemanticProfile(
        profile_id="tech-1", company_id="co-1", company_name="示例科技",
        domain_profile=TechnologyDomainProfile(status="insufficient_evidence", reason="证据不足", registry_version="domain.v1"),
        template_selection=TechnologyTemplateSelection(
            status="selected", selected_template_ids=[template.id],
            evidence=[TemplateEvidence(template_id=template.id, evidence_chunk_ids=["chunk-1"], reason="test")],
            reason="test", registry_version=registry.templates.registry_version,
        ),
        technology_facts=[tech_fact],
        milestone_observations=[MilestoneObservation(
            template_id=template.id, milestone_id=milestone.id, status="limited_support",
            supporting_fact_ids=["t-1"], reason="有限依据", blocked_inferences=["不能推出量产"],
        )],
        processor_version="tech.v1", domain_registry_version="domain.v1",
        template_registry_version=registry.templates.registry_version,
        standard_registry_version="standard.v1", created_at=NOW,
    )
    repo.finance = TechFinanceProfile(
        profile_id="fin-1", company_id="co-1", company_name="示例科技", technology_profile_id="tech-1",
        processor_version="finance.v1", finance_registry_version="finance.v1", created_at=NOW,
    )
    repo.assertions = EvidenceAssertionProfile(
        profile_id="assert-1", company_id="co-1", technology_profile_id="tech-1", finance_profile_id="fin-1",
        technology_assertion_count=0, financial_assertion_count=0, single_source_count=0,
        multi_source_support_count=0, conflict_count=0, processor_version="assert.v1", created_at=NOW,
    )


class EvidenceFirstReportTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        make_profiles(self.repository)
        self.assembler = EvidenceFirstReportAssembler(self.repository)

    def test_build_preserves_limited_milestone_citation_and_is_deterministic(self):
        first = self.assembler.build("co-1")
        second = self.assembler.build("co-1")
        self.assertEqual(first.report_id, second.report_id)
        a, b = first.model_dump(mode="json"), second.model_dump(mode="json")
        a.pop("generated_at")
        b.pop("generated_at")
        self.assertEqual(a, b)
        milestone = first.technology_milestones[0]
        self.assertEqual(milestone.status, "limited_support")
        self.assertEqual(milestone.status_label, "有限支持")
        self.assertEqual(milestone.blocked_inferences, ["不能推出量产"])
        fact = first.technology_profile.representative_facts[0]
        self.assertEqual(fact.evidence.page_number, 2)
        self.assertEqual(fact.evidence.excerpt, "原文节选")
        self.assertEqual(first.evidence_assertions.multi_source_support_count, 0)
        self.assertFalse(first.source_summary.official_website_verified)

    def test_stale_assertion_chain_fails(self):
        self.repository.assertions.technology_profile_id = "old-tech"
        with self.assertRaisesRegex(ValueError, "stale_profile_chain"):
            self.assembler.build("co-1")

    def test_stale_finance_chain_fails(self):
        self.repository.finance.technology_profile_id = "old-tech"
        with self.assertRaisesRegex(ValueError, "stale_profile_chain"):
            self.assembler.build("co-1")

    def test_no_evidence_milestone_remains_visible(self):
        self.repository.tech.milestone_observations[0].status = "no_evidence"
        report = self.assembler.build("co-1")
        self.assertEqual(report.technology_milestones[0].status, "no_evidence")
        self.assertEqual(report.technology_milestones[0].status_label, "暂无证据")

    def test_finance_bundle_expands_fact_citations_and_preserves_limited_status(self):
        registry = self.assembler.finance_registry
        dimension = next(iter(registry.dimension_by_id))
        citation = self.repository.tech.technology_facts[0].citation
        quality = self.repository.tech.technology_facts[0].source_quality
        facts = [FinancialFact(
            fact_id=f"f-{index}", company_id="co-1", subject="企业", predicate="披露",
            object_value=f"值{index}", fact_type="disclosure", source_chunk_id="chunk-1",
            citation=citation, source_quality=quality, financial_dimension=dimension,
            processor_version="finance.v1",
        ) for index in (1, 2)]
        rule = registry.rules[0]
        self.repository.finance.financial_facts = facts
        self.repository.finance.applicable_rule_ids = [rule.rule_id]
        self.repository.finance.funding_activities = [FinancingActivityObservation(
            status="limited_support", funding_activities=["资金活动关注"], reason="依据有限",
            scenario_id=rule.scenario_id,
            evidence_bundle=EvidenceBundle(financial_fact_ids=["f-1", "f-2"], rule_ids=[rule.rule_id]),
        )]
        report = self.assembler.build("co-1")
        item = report.technology_finance_links.funding_activities[0]
        self.assertEqual(item.status, "limited_support")
        self.assertEqual([fact.fact_id for fact in item.evidence.financial_facts], ["f-1", "f-2"])
        self.assertEqual(item.evidence.financial_facts[0].evidence.citation_id, "cit-1")

    def test_missing_cited_source_fails(self):
        self.repository.sources.clear()
        with self.assertRaisesRegex(ValueError, "missing_source"):
            self.assembler.build("co-1")

    def test_financial_gap_deduplicates_dimension_and_merges_fields_and_rules(self):
        dimension = next(iter(self.assembler.finance_registry.dimension_by_id))
        self.repository.finance.financial_information_gaps = [
            FinancialInformationGap(dimension_id=dimension, description="需补材料", requested_fields=["A"], source_rule_ids=["R1"]),
            FinancialInformationGap(dimension_id=dimension, description="需补材料", requested_fields=["B", "A"], source_rule_ids=["R2"]),
        ]
        report = self.assembler.build("co-1")
        self.assertEqual(report.information_gaps.total_gap_count, 2)
        self.assertEqual(report.information_gaps.deduplicated_financial_dimension_count, 1)
        self.assertEqual(report.information_gaps.financial_gaps[0].requested_fields, ["A", "B"])
        self.assertEqual(report.information_gaps.financial_gaps[0].source_rule_ids, ["R1", "R2"])


if __name__ == "__main__":
    unittest.main()
