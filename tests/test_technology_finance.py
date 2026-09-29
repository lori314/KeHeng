"""Offline tests for evidence-bounded technology-finance reasoning."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.finance.contracts import FinancialFact, FinancialFactDraft, MappingSelection, ReasoningGuard
from app.finance.fact_extractor import FinancialFactExtractor
from app.finance.mapper import TechnologyFinanceMapper
from app.finance.registry import FinanceRegistry
from app.finance.validator import TechFinanceValidator
from app.knowledge.contracts import Citation, CitationLocator, KnowledgeChunk, KnowledgeLayer, Source, SourceType, SourceVersion
from app.knowledge.identity import company_for_name
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.knowledge.semantic.contracts import MilestoneObservation, SourceQuality, TechnologyFact, TechnologyDomainProfile, TechnologySemanticProfile, TechnologyTemplateSelection
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.llm import StructuredModelError
from app.research.contracts import SearchResult
from app.research.ingestion import prepare_web_source


class FakeModel:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    async def complete_json(self, prompt, payload):
        self.calls.append(payload)
        return self.callback(prompt, payload)


def make_fact(company_id, fact_type, template, chunk_id="raw-1", category="first_party", dimension="rd_expense"):
    citation = Citation(citation_id="cit-raw", source_id="source-1", source_version_id="sv-1", source_url="https://example.test/report", source_title="企业年报", excerpt="2025年度研发费用8000万元", locator=CitationLocator(page_number=8))
    quality = SourceQuality(category=category, source_type="company_official", content_scope="full_content", rationale="test fixture")
    return TechnologyFact(fact_id=f"tech-{fact_type}", company_id=company_id, subject="企业", predicate=fact_type, object_value=fact_type, fact_type=fact_type, source_chunk_id=chunk_id, citation=citation, template_tags=[template], source_quality=quality, processor_version="test")


def semantic_profile(template="new_materials", milestone="pilot_line", fact_type="pilot_line", lifecycle="unknown"):
    company = company_for_name("测试科技有限公司")
    fact = make_fact(company.company_id, fact_type, template)
    milestone_obs = MilestoneObservation(milestone_id=milestone, template_id=template, status="supported", supporting_fact_ids=[fact.fact_id], reason="source-supported")
    return TechnologySemanticProfile(profile_id="sem-stable", company_id=company.company_id, company_name=company.canonical_name, domain_profile=TechnologyDomainProfile(status="classified", primary_domains=["1"], evidence_chunk_ids=["raw-1"], reason="evidence", registry_version="d1"), template_selection=TechnologyTemplateSelection(status="selected", selected_template_ids=[template], evidence=[{"template_id": template, "evidence_chunk_ids": ["raw-1"], "reason": "evidence"}], reason="evidence", registry_version="t1"), technology_facts=[fact], milestone_observations=[milestone_obs], processor_version="sem1", domain_registry_version="d1", template_registry_version="t1", standard_registry_version="s1", created_at="2026-01-01T00:00:00Z")


def finance_fact(company_id, dimension="rd_expense", category="first_party"):
    citation = make_fact(company_id, "pilot_line", "new_materials").citation
    quality = SourceQuality(category=category, source_type="company_official", content_scope="full_content", rationale="test fixture")
    return FinancialFact(fact_id="fin-rd", company_id=company_id, subject="企业", predicate="研发费用", object_value="2025年度研发费用8000万元", fact_type="financial_disclosure", financial_dimension=dimension, period="2025", quantitative_value=8000, quantitative_unit="万元", currency="CNY", source_chunk_id="raw-1", citation=citation, source_quality=quality, processor_version="test")


def select_candidate(_prompt, payload, *, rule_id=None, include_finance=True):
    candidate = next((x for x in payload["candidates"] if rule_id is None or x["rule"]["rule_id"] == rule_id), None)
    if candidate is None:
        return {"selections": []}
    return {"selections": [{"rule_id": candidate["rule"]["rule_id"], "scenario_id": candidate["rule"]["scenario_id"], "technology_fact_ids": candidate["technology_fact_ids"], "milestone_refs": candidate["milestone_refs"], "financial_fact_ids": candidate["financial_fact_ids"] if include_finance else [], "observation_kinds": candidate["rule"]["output_type"]}]}


class FinanceContractTest(unittest.TestCase):
    def setUp(self):
        self.registry = FinanceRegistry()

    def test_01_joint_technology_and_financial_bundle(self):
        tech = semantic_profile()
        fact = finance_fact(tech.company_id)
        model = FakeModel(lambda p, payload: select_candidate(p, payload, rule_id="new_materials_pilot_maturation"))
        import asyncio
        profile = asyncio.run(TechnologyFinanceMapper(model, self.registry).map(tech, [fact], [], "test"))
        bundle = profile.funding_activities[0].evidence_bundle
        self.assertTrue(bundle.technology_fact_ids and bundle.financial_fact_ids and bundle.rule_ids)

    def test_02_technology_without_finance_yields_gaps(self):
        tech = semantic_profile()
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x, rule_id="new_materials_pilot_maturation", include_finance=False)), self.registry).map(tech, [], [], "test"))
        self.assertTrue(result.funding_activities)
        self.assertIn("cash_flow", {x.dimension_id for x in result.financial_information_gaps})
        self.assertEqual(result.financial_facts, [])

    def test_03_financial_only_does_not_forge_technology_stage(self):
        tech = semantic_profile()
        tech.technology_facts = []
        tech.milestone_observations = []
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x)), self.registry).map(tech, [finance_fact(tech.company_id)], [], "test"))
        self.assertIsNone(result.technology_stage)
        self.assertEqual(result.funding_activities, [])

    def test_04_tapeout_maps_to_validation_not_volume_production(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout")
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x, rule_id="semiconductor_tapeout_validation", include_finance=False)), self.registry).map(tech, [], [], "test"))
        self.assertNotIn("规模量产扩产融资阶段", "".join(item for observation in result.funding_activities for item in observation.funding_activities))
        self.assertIn("volume_production", {x.node_id for x in result.monitoring_nodes})

    def test_05_enterprise_lifecycle_is_not_inferred_from_technology(self):
        tech = semantic_profile("software_ai", "research_prototype", "prototype")
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x)), self.registry).map(tech, [], [], "test"))
        self.assertEqual(result.enterprise_lifecycle, "unknown")

    def test_06_fact_rejects_unknown_chunk_and_clones_citation(self):
        async def exercise():
            company = company_for_name("测试科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            citation = Citation(citation_id="c", source_id="s", source_version_id="v", source_url="https://example.test/report", source_title="披露", excerpt="研发费用为8000万元", locator=CitationLocator(page_number=3))
            chunk = KnowledgeChunk(chunk_id="allowed", text="研发费用为8000万元", source_id="s", source_version_id="v", citation=citation, company_id=company.company_id, knowledge_layer=KnowledgeLayer.GENERAL)
            response = {"facts": [{"source_chunk_id": "not-allowed", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}, {"source_chunk_id": "allowed", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}], "information_gaps": []}
            extractor = FinancialFactExtractor(FakeModel(lambda p, x: response), self.registry)
            facts, _, rejected = await extractor.extract(company.company_id, [chunk], {"s": source}, {"v": version})
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].citation, citation)
            self.assertEqual(facts[0].citation.locator.page_number, 3)
            self.assertEqual(rejected, ["not-allowed"])
        import asyncio
        asyncio.run(exercise())

    def test_07_unknown_rule_id_is_rejected(self):
        tech = semantic_profile()
        async def exercise():
            mapper = TechnologyFinanceMapper(FakeModel(lambda p, x: {"selections": [{"rule_id": "special_super_loan", "scenario_id": "technology_commercialization", "technology_fact_ids": ["tech-pilot_line"], "milestone_refs": ["new_materials:pilot_line"], "financial_fact_ids": [], "observation_kinds": ["funding_activity"]}]}), self.registry)
            with self.assertRaises(ValueError):
                await mapper.map(tech, [], [], "test")
        import asyncio
        asyncio.run(exercise())

    def test_08_approval_fields_fail_closed_schema(self):
        async def exercise():
            async def response(prompt, payload):
                return {"selections": [], "approve": True, "credit_limit": 1000000}
            from app.knowledge.semantic.structured_call import complete_contract
            model = FakeModel(lambda p, x: {"selections": [], "approve": True, "credit_limit": 1})
            with self.assertRaises(StructuredModelError) as caught:
                await complete_contract(model, "p", {}, MappingSelection, stage="test")
            self.assertEqual(caught.exception.category, "schema_failure")
        import asyncio
        asyncio.run(exercise())

    def test_09_citation_chain_keeps_original_url_and_page(self):
        fact = finance_fact(company_for_name("测试科技有限公司").company_id)
        self.assertEqual(fact.citation.source_url, "https://example.test/report")
        self.assertEqual(fact.citation.locator.page_number, 8)

    def test_10_missing_cashflow_is_gap_not_negative_fact(self):
        tech = semantic_profile()
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x)), self.registry).map(tech, [], [], "test"))
        self.assertIn("cash_flow", {x.dimension_id for x in result.financial_information_gaps})
        self.assertFalse(any(x.financial_dimension == "cash_flow" for x in result.financial_facts))
        self.assertFalse(any("较差" in x.description for x in result.financial_information_gaps))

    def test_snippet_only_financial_evidence_cannot_be_strong(self):
        tech = semantic_profile()
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x, rule_id="new_materials_pilot_maturation")), self.registry).map(tech, [finance_fact(tech.company_id, category="snippet_only")], [], "test"))
        self.assertTrue(result.funding_activities)
        self.assertEqual(result.funding_activities[0].status, "limited_support")

    def test_11_innovation_dimensions_are_standard_registry_fields(self):
        self.assertTrue({"rd_expense", "rd_personnel", "patent_count", "technology_contract_value"}.issubset(self.registry.dimension_by_id))
        self.assertNotIn("innovation_total_score", self.registry.dimension_by_id)

    def test_12_fact_and_profile_identity_are_stable(self):
        async def exercise():
            company = company_for_name("测试科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            citation = Citation(citation_id="c", source_id="s", source_version_id="v", source_title="披露", excerpt="研发费用8000万元")
            chunk = KnowledgeChunk(chunk_id="allowed", text="研发费用8000万元", source_id="s", source_version_id="v", citation=citation, company_id=company.company_id)
            response = {"facts": [{"source_chunk_id": "allowed", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}], "information_gaps": []}
            ids = []
            for _ in range(2):
                facts, _, _ = await FinancialFactExtractor(FakeModel(lambda p, x: response), self.registry).extract(company.company_id, [chunk], {"s": source}, {"v": version})
                ids.append(facts[0].fact_id)
            self.assertEqual(ids[0], ids[1])
            tech = semantic_profile()
            outputs = []
            for _ in range(2):
                profile = await TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x)), self.registry).map(tech, [], [], "test")
                outputs.append(profile.profile_id)
            self.assertEqual(outputs[0], outputs[1])
        import asyncio
        asyncio.run(exercise())

    def test_registry_policy_sources_are_metadata_only(self):
        self.assertTrue(all(x.source_url.startswith("https://") and x.source_reference and x.use_boundary for x in self.registry.rules))
        self.assertTrue(self.registry.prohibited_conclusions)

    def test_structural_guard_has_no_credit_decision_fields(self):
        self.assertIn("approve", ReasoningGuard.forbidden_keys)
        self.assertIn("credit_limit", ReasoningGuard.forbidden_keys)

    def test_versioned_profile_round_trips_through_sqlite(self):
        import asyncio
        async def exercise():
            tech = semantic_profile()
            output = await TechnologyFinanceMapper(FakeModel(lambda p, x: select_candidate(p, x, rule_id="new_materials_pilot_maturation")), self.registry).map(tech, [finance_fact(tech.company_id)], [], "test")
            with tempfile.TemporaryDirectory() as folder:
                repository = SQLiteKnowledgeRepository(Path(folder) / "knowledge.sqlite3")
                repository.save_technology_finance_profile(output)
                restored = repository.get_technology_finance_profile(tech.company_id, technology_profile_id=tech.profile_id, processor_version="test", finance_registry_version=self.registry.registry_version)
                self.assertEqual(restored["profile_id"], output.profile_id)
                self.assertEqual(restored["funding_activities"][0]["evidence_bundle"]["financial_fact_ids"], ["fin-rd"])
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
