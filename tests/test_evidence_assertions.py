from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.assertions.contracts import EvidenceAssertion, EvidenceAssertionProfile
from app.knowledge.assertions.normalizer import (
    claim_equivalent,
    normalize_financial_amount,
    normalize_period,
    normalize_subject,
    normalize_text,
    ratio_equivalent,
)
from app.knowledge.assertions.processor import EvidenceAssertionProcessor
from app.knowledge.repository import SQLiteKnowledgeRepository


def _fact(fact_id: str, *, source: str = "source-a", subject: str = "产线", predicate: str = "达到", object_value: str = "稳定量产", fact_type: str = "mass_production", value=None, unit=None, currency=None, period=None, event_time=None, dimension="funding", quality="first_party", scope="full_content"):
    return SimpleNamespace(
        fact_id=fact_id, company_id="company-a", subject=subject, predicate=predicate,
        object_value=object_value, fact_type=fact_type, event_time=event_time,
        quantitative_value=value, quantitative_unit=unit, currency=currency,
        period=period, financial_dimension=dimension, citation=SimpleNamespace(source_id=source),
        source_quality=SimpleNamespace(category=quality, content_scope=scope),
    )


class EvidenceAssertionTest(unittest.TestCase):
    def setUp(self):
        self.processor = EvidenceAssertionProcessor(SimpleNamespace(repository=None))
        self.company = SimpleNamespace(canonical_name="中科寒武纪科技股份有限公司", aliases=["寒武纪"])

    def test_normalization_is_nfkc_casefold_whitespace_and_edge_punctuation_only(self):
        self.assertEqual(normalize_text("  ＡＢＣ  科技， "), "abc 科技")
        self.assertEqual(normalize_text("甲-乙"), "甲-乙")

    def test_technology_same_claim_groups_distinct_sources_but_conflicting_numeric_values_split(self):
        facts = [
            _fact("t1", source="s1", value=85, unit="%"),
            _fact("t2", source="s2", value=85, unit="％"),
            _fact("t3", source="s3", value=92, unit="%"),
        ]
        assertions = self.processor._technology_assertions("company-a", self.company, facts)
        self.assertEqual(len(assertions), 2)
        self.assertTrue(all(item.evidence_status == "conflict" for item in assertions))
        self.assertTrue(all(set(item.conflict_fact_ids) == {"t1", "t2", "t3"} for item in assertions))
        self.assertEqual(sorted(item.supporting_source_count for item in assertions), [1, 2])
        self.assertEqual(len({item.assertion_id for item in assertions}), 2)

    def test_same_source_chunks_count_as_one_source(self):
        assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("t1", source="same"), _fact("t2", source="same"),
        ])
        self.assertEqual(len(assertions), 1)
        self.assertEqual(assertions[0].supporting_source_count, 1)
        self.assertEqual(assertions[0].evidence_status, "single_source")

    def test_cny_units_normalize_and_unknown_units_fail_closed(self):
        self.assertEqual(normalize_financial_amount(1, "亿元", "CNY"), normalize_financial_amount(10000, "万元", "人民币"))
        self.assertNotEqual(normalize_financial_amount(1, "unknown-u", "CNY"), normalize_financial_amount(10000, "unknown-v", "CNY"))
        self.assertTrue(ratio_equivalent("ratio", .15, "%", 15))
        self.assertFalse(ratio_equivalent(None, .15, "%", 15))
        self.assertEqual(normalize_financial_amount(15, "%", None), normalize_financial_amount(.15, "ratio", None))

    def test_broad_industry_claims_keep_the_stricter_threshold(self):
        mode, _, _, _ = claim_equivalent(
            "云端智能芯片", "边缘智能芯片", "行业", "行业",
            industry_or_core_business=True,
        )
        self.assertIsNone(mode)

    def test_company_subject_requires_exact_validated_identity_or_generic_pronoun(self):
        for value in ("中科寒武纪科技股份有限公司", "寒武纪", "公司", "本公司", "该公司", "企业", "本企业", "该企业"):
            self.assertEqual(normalize_subject(value, self.company.canonical_name, self.company.aliases), "__company__")
        self.assertEqual(normalize_subject("寒武纪思元370", self.company.canonical_name, self.company.aliases), "寒武纪思元370")

    def test_period_normalization_covers_year_half_year_and_quarter(self):
        for value in ("2025", "2025年", "2025年度", "2025 年度", "FY2025"):
            self.assertEqual(normalize_period(value), "2025")
        for value in ("2025H1", "2025上半年", "2025年上半年"):
            self.assertEqual(normalize_period(value), "2025-H1")
        for value in ("2025H2", "2025下半年", "2025年下半年"):
            self.assertEqual(normalize_period(value), "2025-H2")
        for value in ("2025Q1", "2025年第一季度", "2025一季度"):
            self.assertEqual(normalize_period(value), "2025-Q1")
        for quarter in range(2, 5):
            self.assertEqual(normalize_period(f"2025Q{quarter}"), f"2025-Q{quarter}")
        self.assertEqual(normalize_period("报告期内"), "报告期内")

    def test_finance_real_wording_alias_and_period_variation_merges_three_sources(self):
        facts = [
            _fact("f1", source="s1", subject="中科寒武纪科技股份有限公司", predicate="营业收入", object_value="10亿元", fact_type="revenue", dimension="revenue", value=10, unit="亿元", currency="CNY", period="2025年度"),
            _fact("f2", source="s2", subject="公司", predicate="实现营业收入", object_value="100000万元", fact_type="revenue", dimension="revenue", value=100000, unit="万元", currency="RMB", period="2025年"),
            _fact("f3", source="s3", subject="寒武纪", predicate="营收达到", object_value="1000000000元", fact_type="revenue", dimension="revenue", value=1000000000, unit="元", currency="CNY", period="FY2025"),
        ]
        assertions = self.processor._finance_assertions("company-a", self.company, facts)
        self.assertEqual(len(assertions), 1)
        self.assertEqual(assertions[0].member_fact_ids, ["f1", "f2", "f3"])
        self.assertEqual(assertions[0].supporting_source_count, 3)
        self.assertEqual(assertions[0].evidence_status, "multi_source_support")
        self.assertEqual(assertions[0].grouping_method, "normalized_quantitative")
        self.assertEqual(self.processor._diagnostics["company_subject_normalized_count"], 3)
        self.assertEqual(self.processor._diagnostics["period_normalized_count"], 3)

    def test_finance_comparable_period_amount_conflicts(self):
        assertions = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", source="s1", subject="公司", dimension="revenue", value=10, unit="亿元", currency="CNY", period="2025"),
            _fact("f2", source="s2", subject="寒武纪", dimension="revenue", value=12, unit="亿元", currency="CNY", period="2025年度"),
        ])
        self.assertEqual(len(assertions), 2)
        self.assertTrue(all(a.evidence_status == "conflict" for a in assertions))
        self.assertTrue(all(a.grouping_method == "conflict_slot" for a in assertions))

    def test_unknown_period_different_amounts_do_not_become_conflict(self):
        assertions = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", subject="公司", dimension="revenue", value=10, unit="亿元", currency="CNY"),
            _fact("f2", source="s2", subject="该公司", dimension="revenue", value=12, unit="亿元", currency="CNY"),
        ])
        self.assertEqual(len(assertions), 2)
        self.assertTrue(all(a.evidence_status == "single_source" for a in assertions))

    def test_financing_without_period_does_not_fuzzy_merge_distinct_institutions(self):
        assertions = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", source="s1", subject="公司", dimension="financing", predicate="融资事件", object_value="本轮融资由甲资本领投"),
            _fact("f2", source="s2", subject="该公司", dimension="financing", predicate="融资事件", object_value="本轮融资由乙资本领投"),
        ])
        self.assertEqual(len(assertions), 2)

    def test_structured_quantitative_assertion_id_is_stable_when_source_is_added(self):
        first = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", source="s1", subject="公司", dimension="revenue", value=10, unit="亿元", currency="CNY", period="2025"),
        ])[0]
        second = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", source="s1", subject="公司", dimension="revenue", value=10, unit="亿元", currency="CNY", period="2025"),
            _fact("f2", source="s2", subject="寒武纪", dimension="revenue", value=100000, unit="万元", currency="RMB", period="FY2025"),
        ])[0]
        self.assertEqual(first.assertion_id, second.assertion_id)
        self.assertEqual(second.evidence_status, "multi_source_support")

    def test_technology_predicate_variation_merges_by_object_containment(self):
        assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("t1", source="s1", subject="中科寒武纪科技股份有限公司", fact_type="product_launch", predicate="发布", object_value="推出新一代云端智能芯片思元370"),
            _fact("t2", source="s2", subject="寒武纪", fact_type="product_launch", predicate="正式推出", object_value="正式推出新一代云端智能芯片思元370"),
        ])
        self.assertEqual(len(assertions), 1)
        self.assertEqual(assertions[0].grouping_method, "containment")
        self.assertEqual(assertions[0].evidence_status, "multi_source_support")

    def test_technology_model_and_number_guards_prevent_false_merges(self):
        model_assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("m1", source="s1", subject="公司", fact_type="product_launch", object_value="推出新一代智能芯片思元370"),
            _fact("m2", source="s2", subject="公司", fact_type="product_launch", object_value="推出新一代智能芯片思元590"),
        ])
        self.assertEqual(len(model_assertions), 2)
        self.assertEqual(self.processor._diagnostics["numeric_guard_rejection_count"], 1)
        self.assertEqual(self.processor._diagnostics["model_anchor_guard_rejection_count"], 0)

        self.processor._diagnostics.clear()
        model_assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("m1", source="s1", subject="公司", fact_type="product_launch", object_value="采用 MLU370 智能芯片完成验证"),
            _fact("m2", source="s2", subject="公司", fact_type="product_launch", object_value="采用 MLU590 智能芯片完成验证"),
        ])
        self.assertEqual(len(model_assertions), 2)
        self.assertEqual(self.processor._diagnostics["model_anchor_guard_rejection_count"], 1)

        self.processor._diagnostics.clear()
        number_assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("n1", source="s1", subject="公司", fact_type="performance", object_value="峰值算力达到256 TOPS"),
            _fact("n2", source="s2", subject="公司", fact_type="performance", object_value="峰值算力达到512 TOPS"),
        ])
        self.assertEqual(len(number_assertions), 2)
        self.assertEqual(self.processor._diagnostics["numeric_guard_rejection_count"], 1)

    def test_different_event_times_are_separate_even_for_identical_claims(self):
        assertions = self.processor._technology_assertions("company-a", self.company, [
            _fact("t1", subject="公司", event_time=datetime(2024, 1, 1, tzinfo=timezone.utc), object_value="推出新一代芯片思元370"),
            _fact("t2", source="s2", subject="公司", event_time=datetime(2025, 1, 1, tzinfo=timezone.utc), object_value="推出新一代芯片思元370"),
        ])
        self.assertEqual(len(assertions), 2)

    def test_text_cluster_compares_only_to_deterministic_anchor_not_transitive_member(self):
        facts = [
            _fact("a", source="s1", object_value="anchor A"),
            _fact("b", source="s2", object_value="bridge B"),
            _fact("c", source="s3", object_value="tail C"),
        ]
        def compare(left, right, *_args, **_kwargs):
            if (left, right) in {("anchor A", "bridge B"), ("bridge B", "tail C")}:
                return "fuzzy_text", .75, False, False
            return None, .5, False, False
        with patch("app.knowledge.assertions.processor.claim_equivalent", side_effect=compare):
            clusters = self.processor._cluster_text_facts(facts, threshold=.72)
        self.assertEqual([len(cluster[0]) for cluster in clusters], [2, 1])
        self.assertEqual(self.processor._diagnostics["candidate_pair_count"], 2)

    def test_finance_comparable_values_are_conflicts_but_unknown_units_are_not(self):
        comparable = self.processor._finance_assertions("company-a", self.company, [
            _fact("f1", source="s1", fact_type="funding", value=1, unit="亿元", currency="CNY", period="2025"),
            _fact("f2", source="s2", fact_type="funding", value=10000, unit="万元", currency="RMB", period="2025"),
            _fact("f3", source="s3", fact_type="funding", value=2, unit="亿元", currency="CNY", period="2025"),
        ])
        self.assertEqual(len(comparable), 2)
        self.assertTrue(all(item.evidence_status == "conflict" for item in comparable))
        unknown = self.processor._finance_assertions("company-a", self.company, [
            _fact("u1", fact_type="funding", value=1, unit="custom-a", currency="CNY", period="2025"),
            _fact("u2", fact_type="funding", value=2, unit="custom-a", currency="CNY", period="2025"),
        ])
        self.assertTrue(all(item.evidence_status != "conflict" for item in unknown))

    def test_repository_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = SQLiteKnowledgeRepository(Path(temp) / "knowledge.sqlite3")
            assertion = EvidenceAssertion(
                assertion_id="a1", company_id="company-a", assertion_domain="technology",
                assertion_type="milestone", representative_fact_id="f1", member_fact_ids=["f1"],
                supporting_source_ids=["s1"], supporting_source_count=1, evidence_status="single_source",
                source_quality_distribution={"first_party": 1}, strongest_source_quality="first_party",
                grouping_method="normalized_quantitative", normalization_key="key", processor_version="evidence-assertions.v2",
            )
            profile = EvidenceAssertionProfile(
                profile_id="p1", company_id="company-a", technology_profile_id="tp1", finance_profile_id="fp1",
                assertions=[assertion], technology_assertion_count=1, financial_assertion_count=0,
                single_source_count=1, multi_source_support_count=0, conflict_count=0,
                processor_version="evidence-assertions.v2", created_at=datetime.now(timezone.utc),
            )
            repository.save_evidence_assertion_profile(profile)
            restored = repository.get_evidence_assertion_profile("company-a", processor_version="evidence-assertions.v2")
            self.assertEqual(restored, profile.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
