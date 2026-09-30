"""Offline tests for evidence-bounded technology-finance reasoning."""

from __future__ import annotations

import hashlib
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.finance.contracts import FinancialFact, FinancialFactDraft, FinancialFactExtractionReport, MappingSelection, ReasoningGuard
from app.finance.fact_extractor import FinancialFactExtractor
from app.finance.mapper import TechnologyFinanceMapper
from app.finance.mapper import _milestone_strength
from app.finance.processor import PROCESSOR_VERSION as FINANCE_PROCESSOR_VERSION, TechnologyFinanceProcessor
from app.finance.registry import FinanceRegistry
from app.finance.validator import TechFinanceValidator
from app.knowledge.contracts import Citation, CitationLocator, KnowledgeChunk, KnowledgeLayer, Source, SourceType, SourceVersion
from app.knowledge.identity import company_for_name
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.knowledge.semantic.contracts import MilestoneObservation, SourceQuality, TechnologyFact, TechnologyDomainProfile, TechnologySemanticProfile, TechnologyTemplateSelection
from app.knowledge.semantic.extractor import TechnologyFactExtractor
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.llm import StructuredModelError
from app.research.contracts import SearchResult
from app.research.ingestion import prepare_web_source


class FakeModel:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    async def complete_json(self, prompt, payload):
        self.calls.append(payload)
        result = self.callback(prompt, payload)
        if hasattr(result, "__await__"):
            return await result
        return result


def make_fact(company_id, fact_type, template, chunk_id="raw-1", category="first_party", dimension="rd_expense"):
    citation = Citation(citation_id="cit-raw", source_id="source-1", source_version_id="sv-1", source_url="https://example.test/report", source_title="企业年报", excerpt="2025年度研发费用8000万元", locator=CitationLocator(page_number=8))
    quality = SourceQuality(category=category, source_type="company_official", content_scope="full_content", rationale="test fixture")
    return TechnologyFact(fact_id=f"tech-{fact_type}", company_id=company_id, subject="企业", predicate=fact_type, object_value=fact_type, fact_type=fact_type, source_chunk_id=chunk_id, citation=citation, template_tags=[template], source_quality=quality, processor_version="test")


def semantic_profile(template="new_materials", milestone="pilot_line", fact_type="pilot_line", lifecycle="unknown", milestone_status="supported"):
    company = company_for_name("测试科技有限公司")
    fact = make_fact(company.company_id, fact_type, template)
    milestone_obs = MilestoneObservation(milestone_id=milestone, template_id=template, status=milestone_status, supporting_fact_ids=[fact.fact_id], reason="source-supported")
    return TechnologySemanticProfile(profile_id="sem-stable", company_id=company.company_id, company_name=company.canonical_name, domain_profile=TechnologyDomainProfile(status="classified", primary_domains=["1"], evidence_chunk_ids=["raw-1"], reason="evidence", registry_version="d1"), template_selection=TechnologyTemplateSelection(status="selected", selected_template_ids=[template], evidence=[{"template_id": template, "evidence_chunk_ids": ["raw-1"], "reason": "evidence"}], reason="evidence", registry_version="t1"), technology_facts=[fact], milestone_observations=[milestone_obs], processor_version="sem1", domain_registry_version="d1", template_registry_version="t1", standard_registry_version="s1", created_at="2026-01-01T00:00:00Z")


def finance_fact(company_id, dimension="rd_expense", category="first_party"):
    citation = make_fact(company_id, "pilot_line", "new_materials").citation
    quality = SourceQuality(category=category, source_type="company_official", content_scope="full_content", rationale="test fixture")
    return FinancialFact(fact_id="fin-rd", company_id=company_id, subject="企业", predicate="研发费用", object_value="2025年度研发费用8000万元", fact_type="financial_disclosure", financial_dimension=dimension, period="2025", quantitative_value=8000, quantitative_unit="万元", currency="CNY", source_chunk_id="raw-1", citation=citation, source_quality=quality, processor_version="test")


class FinanceContractTest(unittest.TestCase):
    def setUp(self):
        self.registry = FinanceRegistry()

    def test_finance_processor_version_tracks_evidence_strength_boundary(self):
        self.assertEqual(FINANCE_PROCESSOR_VERSION, "technology-finance.v4")

    def test_01_joint_technology_and_financial_bundle(self):
        tech = semantic_profile()
        fact = finance_fact(tech.company_id)
        import asyncio
        mapper = TechnologyFinanceMapper(self.registry)
        profile = asyncio.run(mapper.map(tech, [fact], [], "test"))
        bundle = profile.funding_activities[0].evidence_bundle
        self.assertTrue(bundle.technology_fact_ids and bundle.financial_fact_ids and bundle.rule_ids)
        self.assertEqual(mapper.last_execution_trace["mapping_mode"], "deterministic_registry")

    def test_mapper_uses_all_matching_financial_evidence_without_model_dependency(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout")
        financial = [
            finance_fact(tech.company_id, dimension="orders").model_copy(update={"fact_id": f"order-{index}"})
            for index in range(3)
        ]
        mapper = TechnologyFinanceMapper(self.registry)
        import asyncio
        profile = asyncio.run(mapper.map(tech, financial, [], "test"))
        service = next(item for item in profile.monitoring_nodes if item.node_id == "contract_delivery")
        self.assertEqual(service.evidence_bundle.financial_fact_ids, ["order-0", "order-1", "order-2"])
        self.assertFalse(hasattr(mapper, "model"))
        self.assertEqual(mapper.last_execution_trace["mapping_mode"], "deterministic_registry")
        self.assertGreater(mapper.last_execution_trace["milestone_conditioned_candidate_count"], 0)
        self.assertGreater(mapper.last_execution_trace["general_candidate_count"], 0)

    def test_02_technology_without_finance_yields_gaps(self):
        tech = semantic_profile()
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [], [], "test"))
        self.assertTrue(result.funding_activities)
        self.assertIn("cash_flow", {x.dimension_id for x in result.financial_information_gaps})
        self.assertEqual(result.financial_facts, [])

    def test_03_financial_only_does_not_forge_technology_stage(self):
        tech = semantic_profile()
        tech.technology_facts = []
        tech.milestone_observations = []
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [finance_fact(tech.company_id)], [], "test"))
        self.assertIsNone(result.technology_stage)
        self.assertEqual(result.funding_activities, [])

    def test_04_tapeout_maps_to_validation_not_volume_production(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout")
        import asyncio
        mapper = TechnologyFinanceMapper(self.registry)
        result = asyncio.run(mapper.map(tech, [], [], "test"))
        self.assertNotIn("规模量产扩产融资阶段", "".join(item for observation in result.funding_activities for item in observation.funding_activities))
        self.assertIn("volume_production", {x.node_id for x in result.monitoring_nodes})
        bundle = result.monitoring_nodes[0].evidence_bundle
        self.assertIn("semiconductor_design:tapeout", bundle.milestone_refs)
        self.assertIn("tech-tapeout", bundle.technology_fact_ids)
        self.assertIn("semiconductor_tapeout_validation", mapper.last_execution_trace["selected_rule_ids"])
        TechFinanceValidator(self.registry).validate(result, tech)

    def test_05_enterprise_lifecycle_is_not_inferred_from_technology(self):
        tech = semantic_profile("software_ai", "research_prototype", "prototype")
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [], [], "test"))
        self.assertEqual(result.enterprise_lifecycle, "unknown")

    def test_06_fact_rejects_unknown_chunk_and_clones_citation(self):
        async def exercise():
            company = company_for_name("测试科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            citation = Citation(citation_id="c", source_id="s", source_version_id="v", source_url="https://example.test/report", source_title="披露", excerpt="研发费用为8000万元", locator=CitationLocator(page_number=3))
            chunk = KnowledgeChunk(chunk_id="allowed", text="研发费用为8000万元", source_id="s", source_version_id="v", citation=citation, company_id=company.company_id, knowledge_layer=KnowledgeLayer.GENERAL)
            response = {"facts": [{"source_ref": "C9", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}, {"source_ref": "C1", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}, {"source_ref": "C1", "subject": "企业", "predicate": "新造指标", "object_value": "不可接纳", "fact_type": "report", "financial_dimension": "made_up_dimension"}], "information_gaps": []}
            extractor = FinancialFactExtractor(FakeModel(lambda p, x: response), self.registry)
            facts, _, rejected = await extractor.extract(company.company_id, [chunk], {"s": source}, {"v": version})
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].citation, citation)
            self.assertEqual(facts[0].citation.locator.page_number, 3)
            self.assertEqual(facts[0].source_chunk_id, "allowed")
            self.assertEqual(rejected, ["C9"])
            self.assertEqual(extractor.last_report.rejected_evidence_reference_count, 1)
            self.assertEqual(extractor.last_report.rejected_dimension_count, 1)
            self.assertEqual(extractor.last_report.rejected_dimension_distribution, {"made_up_dimension": 1})
            self.assertNotIn("allowed", json.dumps(extractor.model.calls[0]))
            self.assertEqual(extractor.last_report.batch_count, 1)
        import asyncio
        asyncio.run(exercise())

    def test_financial_extractor_bounds_batches_and_concurrency_and_keeps_merge_order(self):
        async def exercise():
            company = company_for_name("批处理合成科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            chunks = [
                KnowledgeChunk(
                    chunk_id=f"real-{index:02d}", text=f"source chunk {index}", source_id="s",
                    source_version_id="v", citation=Citation(citation_id=f"c-{index}", source_id="s", source_version_id="v", source_title="披露", excerpt=f"source chunk {index}"),
                    company_id=company.company_id, knowledge_layer=KnowledgeLayer.GENERAL,
                )
                for index in range(60)
            ]

            class BoundedModel:
                def __init__(self):
                    self.active = 0
                    self.max_active = 0
                    self.calls = []
                    self.release_first = asyncio.Event()

                async def complete_json(self, _prompt, payload):
                    self.calls.append(payload)
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    batch = payload["chunks"]
                    first_text = batch[0]["text"]
                    if first_text == "source chunk 0":
                        await self.release_first.wait()
                    else:
                        self.release_first.set()
                    facts = [{
                        "source_ref": batch[0]["chunk_ref"], "subject": "企业",
                        "predicate": "研发费用", "object_value": first_text,
                        "fact_type": "financial_disclosure", "financial_dimension": "rd_expense",
                    }]
                    self.active -= 1
                    return {"facts": facts, "information_gaps": []}

            model = BoundedModel()
            extractor = FinancialFactExtractor(model, self.registry)
            facts, _, _ = await extractor.extract(
                company.company_id, chunks, {"s": source}, {"v": version}
            )
            self.assertEqual(len(model.calls), 15)
            self.assertTrue(all(len(call["chunks"]) <= 4 for call in model.calls))
            self.assertTrue(all(len({item["chunk_ref"] for item in call["chunks"]}) == len(call["chunks"]) for call in model.calls))
            self.assertTrue(all(item["chunk_ref"] == f"C{position}" for call in model.calls for position, item in enumerate(call["chunks"], 1)))
            self.assertLessEqual(model.max_active, 2)
            self.assertEqual([item.source_chunk_id for item in facts], [f"real-{index:02d}" for index in range(0, 60, 4)])
            self.assertEqual(extractor.last_report.batch_count, 15)
            self.assertEqual(extractor.last_report.successful_chunk_count, 60)
            self.assertEqual(extractor.last_report.max_concurrency, 2)

        asyncio.run(exercise())

    def test_financial_extractor_partial_batch_timeout_keeps_successes_and_reports_gap(self):
        async def exercise():
            company = company_for_name("部分超时合成科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            chunks = [
                KnowledgeChunk(chunk_id=f"id-{i}", text=f"chunk-{i}", source_id="s", source_version_id="v", citation=Citation(citation_id=f"c-{i}", source_id="s", source_version_id="v", source_title="披露", excerpt=f"fact-{i}"), company_id=company.company_id)
                for i in range(9)
            ]

            async def respond(_prompt, payload):
                text = payload["chunks"][0]["text"]
                if text == "chunk-0":
                    raise StructuredModelError("timeout", "safe timeout")
                return {"facts": [{"source_ref": "C1", "subject": "企业", "predicate": "研发费用", "object_value": text, "fact_type": "disclosure", "financial_dimension": "rd_expense"}], "information_gaps": []}

            extractor = FinancialFactExtractor(FakeModel(respond), self.registry)
            facts, gaps, _ = await extractor.extract(company.company_id, chunks, {"s": source}, {"v": version})
            self.assertEqual([item.source_chunk_id for item in facts], ["id-4", "id-8"])
            self.assertTrue(any("1/3 financial fact extraction batches failed" in gap for gap in gaps))
            self.assertEqual(extractor.last_report.successful_batch_count, 2)
            self.assertEqual(extractor.last_report.failed_batch_count, 1)
            self.assertEqual(extractor.last_report.failed_chunk_count, 4)
            self.assertEqual(extractor.last_report.error_categories, {"timeout": 1})

        asyncio.run(exercise())

    def test_financial_extractor_all_batches_failed_raises_category_with_report(self):
        async def exercise():
            company = company_for_name("全失败合成科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            chunks = [
                KnowledgeChunk(chunk_id=f"id-{i}", text=f"chunk-{i}", source_id="s", source_version_id="v", citation=Citation(citation_id=f"c-{i}", source_id="s", source_version_id="v", source_title="披露", excerpt=f"fact-{i}"), company_id=company.company_id)
                for i in range(5)
            ]
            extractor = FinancialFactExtractor(
                FakeModel(lambda _prompt, _payload: (_ for _ in ()).throw(StructuredModelError("network", "safe network error"))),
                self.registry,
            )
            with self.assertRaises(StructuredModelError) as caught:
                await extractor.extract(company.company_id, chunks, {"s": source}, {"v": version})
            self.assertEqual(caught.exception.category, "network")
            self.assertEqual(extractor.last_report.batch_count, 2)
            self.assertEqual(extractor.last_report.successful_batch_count, 0)
            self.assertEqual(extractor.last_report.error_categories, {"network": 2})
            self.assertEqual(extractor.last_execution_trace["financial_fact_count"], 0)

        asyncio.run(exercise())

    def test_financial_extractor_stable_fact_id_does_not_depend_on_local_reference(self):
        async def exercise():
            company = company_for_name("稳定 ID 合成科技有限公司")
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            other = KnowledgeChunk(chunk_id="other-real-id", text="other", source_id="s", source_version_id="v", citation=Citation(citation_id="c-other", source_id="s", source_version_id="v", source_title="披露", excerpt="other"), company_id=company.company_id)
            target = KnowledgeChunk(chunk_id="target-real-id", text="target fact", source_id="s", source_version_id="v", citation=Citation(citation_id="c-target", source_id="s", source_version_id="v", source_title="披露", excerpt="target fact"), company_id=company.company_id)

            def response(_prompt, payload):
                item = next(item for item in payload["chunks"] if item["text"] == "target fact")
                return {"facts": [{"source_ref": item["chunk_ref"], "subject": "企业", "predicate": "研发费用", "object_value": "2025 年研发费用 8,000 万元", "fact_type": "report", "financial_dimension": "rd_expense", "period": "2025", "quantitative_value": 8000, "quantitative_unit": "万元", "currency": "CNY"}], "information_gaps": []}

            single, _, _ = await FinancialFactExtractor(FakeModel(response), self.registry).extract(company.company_id, [target], {"s": source}, {"v": version})
            shifted, _, _ = await FinancialFactExtractor(FakeModel(response), self.registry).extract(company.company_id, [other, target], {"s": source}, {"v": version})
            self.assertEqual(single[0].fact_id, shifted[0].fact_id)
            self.assertEqual(shifted[0].source_chunk_id, "target-real-id")

        asyncio.run(exercise())

    def test_finance_processor_trace_records_all_batch_timeout_at_extraction_stage(self):
        class Repository:
            def __init__(self, company, profile, chunks, source, version):
                self.company, self.profile, self.chunks = company, profile, chunks
                self.source, self.version = source, version

            def get_company(self, _company_id):
                return self.company

            def get_technology_semantic_profile(self, _company_id):
                return self.profile.model_dump(mode="json")

            def list_current_chunks(self, _company_id, layer):
                return self.chunks if layer == KnowledgeLayer.GENERAL.value else []

            def get_source(self, source_id):
                return self.source if source_id == self.source.source_id else None

            def get_source_version(self, version_id):
                return self.version if version_id == self.version.source_version_id else None

        async def exercise():
            tech = semantic_profile()
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            chunks = [
                KnowledgeChunk(chunk_id=f"source-{i}", text=f"chunk {i}", source_id="s", source_version_id="v", citation=Citation(citation_id=f"cite-{i}", source_id="s", source_version_id="v", source_title="披露", excerpt=f"chunk {i}"), company_id=tech.company_id, knowledge_layer=KnowledgeLayer.GENERAL)
                for i in range(5)
            ]
            repository = Repository(
                company_for_name(tech.company_name), tech, chunks, source, version
            )
            processor = TechnologyFinanceProcessor(
                FakeModel(lambda _prompt, _payload: (_ for _ in ()).throw(StructuredModelError("timeout", "safe timeout"))),
                type("KnowledgeBase", (), {"repository": repository})(),
                self.registry,
            )
            with self.assertRaises(StructuredModelError):
                await processor.process_company(tech.company_id)
            self.assertEqual(processor.last_execution_trace["failed_stage"], "financial_fact_extraction")
            self.assertEqual(processor.last_execution_trace["error_category"], "timeout")
            self.assertEqual(processor.last_execution_trace["financial_fact_batch_count"], 2)
            self.assertEqual(processor.last_execution_trace["financial_fact_failed_batches"], 2)

        asyncio.run(exercise())

    def test_processor_sends_llm_call_to_fact_extractor_only(self):
        class Repository:
            def __init__(self, company, profile, chunks, source, version):
                self.company, self.profile, self.chunks = company, profile, chunks
                self.source, self.version = source, version
                self.saved = None

            def get_company(self, _company_id):
                return self.company

            def get_technology_semantic_profile(self, _company_id):
                return self.profile.model_dump(mode="json")

            def list_current_chunks(self, _company_id, layer):
                return self.chunks if layer == KnowledgeLayer.GENERAL.value else []

            def get_source(self, source_id):
                return self.source if source_id == self.source.source_id else None

            def get_source_version(self, version_id):
                return self.version if version_id == self.version.source_version_id else None

            def save_technology_finance_profile(self, profile):
                self.saved = profile

        async def exercise():
            tech = semantic_profile()
            source = Source(source_id="s", source_type=SourceType.COMPANY_OFFICIAL, title="披露", canonical_url="https://example.test/report")
            version = SourceVersion(source_version_id="v", source_id="s", content_sha256="a" * 64, retrieved_at="2026-01-01T00:00:00Z")
            chunk = KnowledgeChunk(
                chunk_id="source-1", text="订单金额已披露", source_id="s", source_version_id="v",
                citation=Citation(citation_id="cite-1", source_id="s", source_version_id="v", source_title="披露", excerpt="订单金额已披露"),
                company_id=tech.company_id, knowledge_layer=KnowledgeLayer.GENERAL,
            )
            repository = Repository(company_for_name(tech.company_name), tech, [chunk], source, version)
            model = FakeModel(lambda _prompt, payload: {
                "facts": [{
                    "source_ref": payload["chunks"][0]["chunk_ref"], "subject": "企业",
                    "predicate": "获得订单", "object_value": "订单金额已披露",
                    "fact_type": "order_disclosure", "financial_dimension": "orders",
                }],
                "information_gaps": [],
            })
            processor = TechnologyFinanceProcessor(
                model, type("KnowledgeBase", (), {"repository": repository})(), self.registry,
            )
            await processor.process_company(tech.company_id)
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(processor.last_execution_trace["mapping_mode"], "deterministic_registry")
            self.assertEqual(processor.last_execution_trace["finance_mapping_status"], "completed")
            self.assertIsNotNone(repository.saved)


    def test_07_unknown_rule_id_is_rejected(self):
        tech = semantic_profile()
        mapper = TechnologyFinanceMapper(self.registry)
        selection = MappingSelection(selections=[{
            "rule_id": "special_super_loan", "scenario_id": "technology_commercialization",
            "technology_fact_ids": ["tech-pilot_line"], "milestone_refs": ["new_materials:pilot_line"],
            "financial_fact_ids": [], "observation_kinds": ["funding_activity"],
        }])
        with self.assertRaisesRegex(RuntimeError, "deterministic mapping invariant violated"):
            mapper._assemble(tech, [], [], "test", selection, [],
                {item.fact_id: item for item in tech.technology_facts}, {},
                {(item.template_id, item.milestone_id): item for item in tech.milestone_observations})

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
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [], [], "test"))
        self.assertIn("cash_flow", {x.dimension_id for x in result.financial_information_gaps})
        self.assertFalse(any(x.financial_dimension == "cash_flow" for x in result.financial_facts))
        self.assertFalse(any("较差" in x.description for x in result.financial_information_gaps))

    def test_weak_web_and_snippet_only_financial_evidence_cannot_be_strong(self):
        tech = semantic_profile()
        import asyncio
        for category in ("snippet_only", "weak_web"):
            with self.subTest(category=category):
                result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [finance_fact(tech.company_id, category=category)], [], "test"))
                self.assertTrue(result.funding_activities)
                self.assertEqual(result.funding_activities[0].status, "limited_support")

    def test_mixed_authoritative_and_weak_finance_can_remain_supported(self):
        tech = semantic_profile()
        weak = finance_fact(tech.company_id, category="weak_web")
        strong = finance_fact(tech.company_id, category="authoritative_public_record").model_copy(update={"fact_id": "fin-strong"})
        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, [weak, strong], [], "test"))
        self.assertEqual(result.funding_activities[0].status, "supported")

    def test_limited_tapeout_does_not_trigger_stage_finance_rule(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "product_launch", milestone_status="limited_support")
        products = [
            tech.technology_facts[0].model_copy(update={"fact_id": f"product-{index}", "fact_type": "product_launch"})
            for index in range(5)
        ]
        tech.technology_facts = products
        tech.milestone_observations[0].supporting_fact_ids = [item.fact_id for item in products]
        weak_financial = [
            finance_fact(tech.company_id, dimension=dimension, category="weak_web").model_copy(update={"fact_id": f"fin-{dimension}"})
            for dimension in ("orders", "customers", "rd_expense")
        ]

        import asyncio
        mapper = TechnologyFinanceMapper(self.registry)
        result = asyncio.run(mapper.map(tech, weak_financial, [], "test"))
        candidate_ids = set(mapper.last_execution_trace["candidate_rule_ids"])
        self.assertNotIn("semiconductor_tapeout_validation", candidate_ids)
        self.assertIn("technology_services_contracts", candidate_ids)
        self.assertNotIn("semiconductor_tapeout_validation", result.applicable_rule_ids)
        self.assertFalse(result.risk_observations)
        self.assertFalse({"silicon_validation", "customer_validation", "volume_production"} & {item.node_id for item in result.monitoring_nodes})
        self.assertTrue(all(item.status == "limited_support" for item in result.monitoring_nodes))

    def test_conflict_tapeout_does_not_trigger_stage_finance_rule(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout", milestone_status="conflict")
        financial = [finance_fact(tech.company_id, dimension="orders")]
        import asyncio
        mapper = TechnologyFinanceMapper(self.registry)
        asyncio.run(mapper.map(tech, financial, [], "test"))
        self.assertNotIn("semiconductor_tapeout_validation", mapper.last_execution_trace["candidate_rule_ids"])

    def test_mapper_restores_verified_milestone_and_required_technology_facts(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout")
        financial = [finance_fact(tech.company_id, dimension="orders")]

        import asyncio
        result = asyncio.run(TechnologyFinanceMapper(self.registry).map(tech, financial, [], "test"))
        bundle = result.monitoring_nodes[0].evidence_bundle
        self.assertEqual(bundle.milestone_refs, ["semiconductor_design:tapeout"])
        self.assertIn("tech-tapeout", bundle.technology_fact_ids)

    def test_assemble_does_not_upgrade_limited_or_conflicting_milestones(self):
        for milestone_status, expected_status, safe_phrase in (
            ("limited_support", "limited_support", "不能认定该里程碑已经发生"),
            ("conflict", "conflict", "当前不能确认其状态"),
        ):
            with self.subTest(milestone_status=milestone_status):
                tech = semantic_profile("semiconductor_design", "tapeout", "tapeout", milestone_status=milestone_status)
                finance = finance_fact(tech.company_id, dimension="orders")
                rule = self.registry.rule_by_id["semiconductor_tapeout_validation"]
                ref = "semiconductor_design:tapeout"
                entry = {
                    "rule": rule.model_dump(mode="json"),
                    "technology_fact_ids": ["tech-tapeout"],
                    "milestone_refs": [ref],
                    "milestones": [{"ref": ref, "status": milestone_status}],
                    "financial_fact_ids": [finance.fact_id],
                }
                selection = MappingSelection(selections=[{
                    "rule_id": rule.rule_id,
                    "scenario_id": rule.scenario_id,
                    "technology_fact_ids": ["tech-tapeout"],
                    "milestone_refs": [ref],
                    "financial_fact_ids": [finance.fact_id],
                    "observation_kinds": rule.output_type,
                }])
                result = TechnologyFinanceMapper(self.registry)._assemble(
                    tech, [finance], [], "test", selection, [entry],
                    {item.fact_id: item for item in tech.technology_facts},
                    {finance.fact_id: finance},
                    {(item.template_id, item.milestone_id): item for item in tech.milestone_observations},
                )
                self.assertEqual(result.funding_activities, [])
                self.assertEqual(result.risk_observations[0].status, expected_status)
                self.assertIn(safe_phrase, result.risk_observations[0].reason)
                self.assertTrue(all(item.status == expected_status for item in result.monitoring_nodes))

    def test_validator_rejects_milestone_provenance_gaps_and_overstated_status(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout")
        financial = finance_fact(tech.company_id, dimension="orders")
        import asyncio
        base = asyncio.run(TechnologyFinanceMapper(self.registry,
        ).map(tech, [financial], [], "test"))
        validator = TechFinanceValidator(self.registry)
        validator.validate(base, tech)

        risk = base.risk_observations[0]
        no_refs = risk.model_copy(update={"evidence_bundle": risk.evidence_bundle.model_copy(update={"milestone_refs": []})})
        with self.assertRaisesRegex(ValueError, "missing milestone provenance"):
            validator.validate(base.model_copy(update={"risk_observations": [no_refs]}), tech)

        wrong_trigger = risk.model_copy(update={
            "evidence_bundle": risk.evidence_bundle.model_copy(update={"milestone_refs": ["semiconductor_design:prototype"]})
        })
        with self.assertRaisesRegex(ValueError, "unknown source evidence"):
            validator.validate(base.model_copy(update={"risk_observations": [wrong_trigger]}), tech)

        missing_fact = risk.model_copy(update={
            "evidence_bundle": risk.evidence_bundle.model_copy(update={"technology_fact_ids": []})
        })
        with self.assertRaisesRegex(ValueError, "missing technology facts"):
            validator.validate(base.model_copy(update={"risk_observations": [missing_fact]}), tech)

        limited_tech = tech.model_copy(update={
            "milestone_observations": [tech.milestone_observations[0].model_copy(update={"status": "limited_support"})]
        })
        with self.assertRaisesRegex(ValueError, "not supported"):
            validator.validate(base, limited_tech)

        limited_funding = base.funding_activities[0].model_copy(update={"status": "limited_support"})
        limited_risks = [item.model_copy(update={"status": "limited_support"}) for item in base.risk_observations]
        limited_monitors = [item.model_copy(update={"status": "limited_support"}) for item in base.monitoring_nodes]
        with self.assertRaisesRegex(ValueError, "funding activity references"):
            validator.validate(base.model_copy(update={
                "funding_activities": [limited_funding],
                "risk_observations": limited_risks,
                "monitoring_nodes": limited_monitors,
            }), limited_tech)

    def test_milestone_strength_priority_is_deterministic(self):
        tech = semantic_profile("semiconductor_design", "tapeout", "tapeout", milestone_status="limited_support")
        milestones = {(item.template_id, item.milestone_id): item for item in tech.milestone_observations}
        self.assertEqual(_milestone_strength(["semiconductor_design:tapeout"], milestones, required=True), "limited_support")
        conflict = tech.milestone_observations[0].model_copy(update={"status": "conflict"})
        milestones[(conflict.template_id, conflict.milestone_id)] = conflict
        self.assertEqual(_milestone_strength(["semiconductor_design:tapeout"], milestones, required=True), "conflict")
        self.assertEqual(_milestone_strength([], milestones, required=True), "insufficient_evidence")

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
            response = {"facts": [{"source_ref": "C1", "subject": "企业", "predicate": "研发费用", "object_value": "8000万元", "fact_type": "report", "financial_dimension": "rd_expense"}], "information_gaps": []}
            ids = []
            for _ in range(2):
                facts, _, _ = await FinancialFactExtractor(FakeModel(lambda p, x: response), self.registry).extract(company.company_id, [chunk], {"s": source}, {"v": version})
                ids.append(facts[0].fact_id)
            self.assertEqual(ids[0], ids[1])
            tech = semantic_profile()
            outputs = []
            for _ in range(2):
                profile = await TechnologyFinanceMapper(self.registry).map(tech, [], [], "test")
                outputs.append(profile.profile_id)
            self.assertEqual(outputs[0], outputs[1])
        import asyncio
        asyncio.run(exercise())

    def test_technology_type_filter_does_not_block_financial_extraction_from_same_general_chunk(self):
        async def exercise():
            company = company_for_name("职责边界合成科技有限公司")
            source = Source(
                source_id="boundary-source", source_type=SourceType.COMPANY_OFFICIAL,
                title="合成年度披露", canonical_url="https://example.test/boundary",
            )
            version = SourceVersion(
                source_version_id="boundary-version", source_id=source.source_id,
                content_sha256="b" * 64, retrieved_at="2026-01-01T00:00:00Z",
            )
            citation = Citation(
                citation_id="boundary-citation", source_id=source.source_id,
                source_version_id=version.source_version_id,
                source_url=source.canonical_url, source_title=source.title,
                excerpt="营业收入5亿元，净利润5000万元，客户订单3亿元，研发费用8000万元。",
                locator=CitationLocator(page_number=2),
            )
            chunk = KnowledgeChunk(
                chunk_id="boundary-general", text=citation.excerpt or "",
                source_id=source.source_id, source_version_id=version.source_version_id,
                citation=citation, company_id=company.company_id,
                knowledge_layer=KnowledgeLayer.GENERAL,
            )
            knowledge_registry = KnowledgeSemanticRegistry()
            domain = TechnologyDomainProfile(
                status="classified", primary_domains=["1"],
                evidence_chunk_ids=[chunk.chunk_id], reason="合成披露", registry_version="d1",
            )
            selection = TechnologyTemplateSelection(
                status="selected", selected_template_ids=["semiconductor_design"],
                evidence=[{"template_id":"semiconductor_design","evidence_chunk_ids":[chunk.chunk_id],"reason":"合成披露"}],
                reason="合成披露", registry_version=knowledge_registry.templates.registry_version,
            )
            business_types = ["revenue", "financial_profit", "customer_order", "rd_expense"]
            tech_response = {"facts": [
                {
                    "source_chunk_id": chunk.chunk_id, "subject": "企业", "predicate": "披露",
                    "object_value": value, "fact_type": fact_type, "event_time": None,
                    "quantitative_value": None, "quantitative_unit": None,
                    "domain_tags": ["1"], "template_tags": ["semiconductor_design"],
                }
                for fact_type, value in zip(
                    business_types,
                    ("营业收入5亿元", "净利润5000万元", "客户订单3亿元", "研发费用8000万元"),
                    strict=True,
                )
            ], "information_gaps": []}
            technology_facts, _, _, _, technology_report = await TechnologyFactExtractor(
                FakeModel(lambda _prompt, _payload: tech_response),
                knowledge_registry, processor_version="test.v1",
            ).extract(
                company_id=company.company_id,
                domain_profile=domain,
                selection=selection,
                chunks=[chunk],
                sources={source.source_id: source},
                versions={version.source_version_id: version},
            )
            self.assertEqual(technology_facts, [])
            self.assertEqual(technology_report.rejected_fact_type_count, 4)

            finance_response = {"facts": [
                {"source_ref": "C1", "subject": "企业", "predicate": predicate,
                 "object_value": value, "fact_type": "financial_disclosure",
                 "financial_dimension": dimension, "period": "2025", "event_time": None,
                 "quantitative_value": amount, "quantitative_unit": unit, "currency": "CNY"}
                for predicate, value, dimension, amount, unit in (
                    ("营业收入", "营业收入5亿元", "revenue", 5, "亿元"),
                    ("净利润", "净利润5000万元", "profitability", 5000, "万元"),
                    ("客户订单", "客户订单3亿元", "technology_contract_value", 3, "亿元"),
                    ("研发费用", "研发费用8000万元", "rd_expense", 8000, "万元"),
                )
            ], "information_gaps": []}
            financial_facts, _, rejected = await FinancialFactExtractor(
                FakeModel(lambda _prompt, _payload: finance_response), self.registry
            ).extract(
                company.company_id, [chunk], {source.source_id: source},
                {version.source_version_id: version},
            )
            self.assertEqual(rejected, [])
            self.assertEqual(len(financial_facts), 4)
            self.assertEqual(
                {item.financial_dimension for item in financial_facts},
                {"revenue", "profitability", "technology_contract_value", "rd_expense"},
            )
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
            output = await TechnologyFinanceMapper(self.registry).map(tech, [finance_fact(tech.company_id)], [], "test")
            output = output.model_copy(update={
                "financial_fact_extraction_report": FinancialFactExtractionReport(
                    batch_count=2, successful_batch_count=1, failed_batch_count=1,
                    input_chunk_count=5, successful_chunk_count=1, failed_chunk_count=4,
                    fact_count=1, batch_size=4, max_concurrency=2,
                    error_categories={"timeout": 1},
                ),
            })
            with tempfile.TemporaryDirectory() as folder:
                repository = SQLiteKnowledgeRepository(Path(folder) / "knowledge.sqlite3")
                repository.save_technology_finance_profile(output)
                restored = repository.get_technology_finance_profile(tech.company_id, technology_profile_id=tech.profile_id, processor_version="test", finance_registry_version=self.registry.registry_version)
                self.assertEqual(restored["profile_id"], output.profile_id)
                self.assertEqual(restored["funding_activities"][0]["evidence_bundle"]["financial_fact_ids"], ["fin-rd"])
                self.assertEqual(restored["financial_fact_extraction_report"]["failed_chunk_count"], 4)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
