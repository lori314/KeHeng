"""Offline contract and pipeline tests for adaptive technology semantics."""

from __future__ import annotations

import sys
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.contracts import KnowledgeLayer
from app.knowledge.identity import company_for_name, derived_fact_chunk_id_for
from app.knowledge.semantic.contracts import (
    MilestoneObservation,
    SourceQuality,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.interpreter import TechnologyInterpreter
from app.knowledge.semantic.extractor import TechnologyFactExtractor
from app.knowledge.semantic.classifier import TechnologyDomainClassifier
from app.knowledge.semantic.processor import TechnologyKnowledgeProcessor
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.shared_knowledge_base import (
    KnowledgeSearchRequest,
    SharedKnowledgeBase,
)
from app.research.contracts import SearchResult
from app.research.ingestion import prepare_web_source
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.llm import StructuredModelError


class FakeStructuredModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, system_prompt, payload):
        self.calls.append({"prompt": system_prompt, "payload": payload})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def classifier_result(evidence_ref="E1", *, domains=("1",), templates=("software_ai",), status="classified"):
    return {
        "status": status,
        "primary_domain_ids": list(domains),
        "secondary_domain_ids": [],
        "domain_evidence_refs": [evidence_ref] if status == "classified" else [],
        "domain_reason": "来源正文描述了与分类相符的技术产品。" if status == "classified" else "缺少可核验技术证据。",
        "selected_template_ids": list(templates),
        "template_evidence": [
            {"template_id": item, "evidence_refs": [evidence_ref], "reason": "正文描述了相应研发对象。"}
            for item in templates
        ],
        "template_reason": "根据同一来源中分别披露的软件与器械技术对象组合选择。" if templates else "资料不足。",
        "information_gaps": [],
    }


def fact_output(chunk_id, *, fact_type="prototype", templates=("software_ai",)):
    return {
        "facts": [
            {
                "source_chunk_id": chunk_id,
                "subject": "智能影像系统",
                "predicate": "完成",
                "object_value": "临床验证样机测试",
                "fact_type": fact_type,
                "event_time": None,
                "quantitative_value": None,
                "quantitative_unit": None,
                "domain_tags": ["1"],
                "template_tags": list(templates),
            }
        ],
        "information_gaps": [],
    }


def interpreter_result(
    template_id="software_ai",
    milestone_id="research_prototype",
    *,
    status="supported",
    reason="输入事实支持观察到该里程碑。",
    supporting_fact_refs=(),
    contradicting_fact_refs=(),
):
    return {
        "observations": [
            {
                "milestone_id": milestone_id,
                "template_id": template_id,
                "status": status,
                "supporting_fact_refs": list(supporting_fact_refs),
                "contradicting_fact_refs": list(contradicting_fact_refs),
                "reason": reason,
            }
        ],
        "information_gaps": [],
    }


class TechnologySemanticTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.kb = SharedKnowledgeBase(
            Path(self.tempdir.name),
            embedding_provider=LocalHashingEmbeddingProvider(dimensions=256),
        )

    async def asyncTearDown(self):
        self.kb.close()
        self.tempdir.cleanup()

    async def test_extractor_batches_18_chunks_with_bounded_concurrency_and_stable_merge_order(self):
        company, chunks, sources, versions, registry, domain, selection = await self.extractor_inputs(18)
        second_batch_finished = asyncio.Event()

        class OrderedConcurrentModel:
            def __init__(self):
                self.calls = []
                self.active = 0
                self.max_active = 0
                self.completion_order = []

            async def complete_json(self, _prompt, payload):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                batch_chunk = payload["general_chunks"][0]
                index = chunks.index(next(chunk for chunk in chunks if chunk.chunk_id == batch_chunk["chunk_id"])) // 3
                self.calls.append(payload)
                if index == 0:
                    await second_batch_finished.wait()
                elif index == 1:
                    self.completion_order.append(index)
                    second_batch_finished.set()
                self.active -= 1
                if index != 1:
                    self.completion_order.append(index)
                return fact_output(batch_chunk["chunk_id"])

        model = OrderedConcurrentModel()
        extractor = TechnologyFactExtractor(model, registry, processor_version="test.v1")
        facts, gaps, rejected, rejected_tags, report = await extractor.extract(
            company_id=company.company_id, domain_profile=domain, selection=selection,
            chunks=chunks, sources=sources, versions=versions,
        )
        self.assertEqual(report.batch_count, 6)
        self.assertEqual(report.successful_batch_count, 6)
        self.assertEqual(len(model.calls), 6)
        self.assertTrue(all(len(call["general_chunks"]) <= 3 for call in model.calls))
        self.assertEqual(
            [item["chunk_id"] for call in model.calls for item in call["general_chunks"]],
            [chunk.chunk_id for chunk in chunks],
        )
        self.assertLessEqual(model.max_active, 2)
        self.assertEqual(model.max_active, 2)
        self.assertLess(model.completion_order.index(1), model.completion_order.index(0))
        self.assertEqual([fact.source_chunk_id for fact in facts], [chunks[i].chunk_id for i in range(0, 18, 3)])
        self.assertEqual((gaps, rejected, rejected_tags), ([], [], []))

    async def test_extractor_partial_timeout_fails_closed_and_reports_affected_evidence(self):
        company, chunks, sources, versions, registry, domain, selection = await self.extractor_inputs(18)
        batch_indices = {chunk.chunk_id: index // 3 for index, chunk in enumerate(chunks)}

        class OneTimeoutModel:
            async def complete_json(self, _prompt, payload):
                chunk_id = payload["general_chunks"][0]["chunk_id"]
                if batch_indices[chunk_id] == 2:
                    raise StructuredModelError("timeout", "synthetic timeout")
                return fact_output(chunk_id)

        extractor = TechnologyFactExtractor(OneTimeoutModel(), registry, processor_version="test.v1")
        facts, gaps, _rejected, _tags, report = await extractor.extract(
            company_id=company.company_id, domain_profile=domain, selection=selection,
            chunks=chunks, sources=sources, versions=versions,
        )
        self.assertEqual((report.successful_batch_count, report.failed_batch_count), (5, 1))
        self.assertEqual((report.successful_chunk_count, report.failed_chunk_count), (15, 3))
        self.assertEqual(report.error_categories, {"timeout": 1})
        self.assertNotIn(chunks[6].chunk_id, [fact.source_chunk_id for fact in facts])
        self.assertTrue(any("1/6 fact extraction batches failed" in gap for gap in gaps))

    async def test_extractor_all_batch_timeouts_raise_original_category(self):
        company, chunks, sources, versions, registry, domain, selection = await self.extractor_inputs(18)

        class TimeoutModel:
            async def complete_json(self, _prompt, _payload):
                raise StructuredModelError("timeout", "synthetic timeout")

        extractor = TechnologyFactExtractor(TimeoutModel(), registry, processor_version="test.v1")
        with self.assertRaises(StructuredModelError) as raised:
            await extractor.extract(
                company_id=company.company_id, domain_profile=domain, selection=selection,
                chunks=chunks, sources=sources, versions=versions,
            )
        self.assertEqual(raised.exception.category, "timeout")
        self.assertEqual(extractor.last_report.failed_batch_count, 6)

    async def test_extractor_rejects_reference_to_another_selected_batch(self):
        company, chunks, sources, versions, registry, domain, selection = await self.extractor_inputs(18)

        class CrossBatchModel:
            async def complete_json(self, _prompt, payload):
                first_id = payload["general_chunks"][0]["chunk_id"]
                if first_id == chunks[0].chunk_id:
                    return fact_output(chunks[6].chunk_id)
                return {"facts": [], "information_gaps": []}

        extractor = TechnologyFactExtractor(CrossBatchModel(), registry, processor_version="test.v1")
        facts, _gaps, rejected, _tags, report = await extractor.extract(
            company_id=company.company_id, domain_profile=domain, selection=selection,
            chunks=chunks, sources=sources, versions=versions,
        )
        self.assertEqual(facts, [])
        self.assertEqual(rejected, [chunks[6].chunk_id])
        self.assertEqual(report.rejected_evidence_reference_count, 1)
        self.assertEqual(report.error_categories, {"invalid_batch_chunk_reference": 1})

    async def add_general_chunk(self, company_name, content, *, snippet=False, url_suffix="technology"):
        company = company_for_name(company_name)
        slug = hashlib.sha256(company_name.encode("utf-8")).hexdigest()[:12]
        raw_content = (content + "\n\n" + "公开技术资料持续说明产品研发与验证过程。" * 30) if not snippet else None
        result = SearchResult(
            title="企业技术披露",
            url=f"https://example.test/{slug}/{url_suffix}",
            content=content[:260],
            raw_content=raw_content,
            provider="test",
        )
        prepared = prepare_web_source(
            result,
            enterprise_name=company_name,
            company=company,
        )
        await self.kb.upsert_source_version(
            prepared.source,
            prepared.source_version,
            prepared.chunks,
            company=company,
        )
        return company, prepared.chunks

    async def extractor_inputs(self, count):
        company_name = "批处理合成企业"
        company = None
        chunks = []
        sources = {}
        versions = {}
        for index in range(count):
            company, prepared_chunks = await self.add_general_chunk(
                company_name,
                f"批次证据{index}披露了不同芯片产品的架构设计、研发测试和验证进展。",
                url_suffix=f"batch-{index}",
            )
            chunk = prepared_chunks[0]
            chunks.append(chunk)
            sources[chunk.source_id] = self.kb.repository.get_source(chunk.source_id)
            versions[chunk.source_version_id] = self.kb.repository.get_source_version(chunk.source_version_id)
        registry = KnowledgeSemanticRegistry()
        domain = TechnologyDomainProfile(
            status="classified", primary_domains=["1"], evidence_chunk_ids=[chunks[0].chunk_id],
            reason="合成测试分类。", registry_version=registry.domains.registry_version,
        )
        selection = TechnologyTemplateSelection(
            status="selected", selected_template_ids=["software_ai"],
            evidence=[{"template_id": "software_ai", "evidence_chunk_ids": [chunks[0].chunk_id], "reason": "合成测试选择。"}],
            reason="合成测试选择。", registry_version=registry.templates.registry_version,
        )
        return company, chunks, sources, versions, registry, domain, selection

    async def test_registry_has_official_nine_domains_six_composable_templates_and_standard_metadata(self):
        registry = KnowledgeSemanticRegistry()
        self.assertEqual(len(registry.domains.domains), 9)
        self.assertEqual(registry.domain_by_id["1"].name, "新一代信息技术产业")
        self.assertEqual(registry.domain_by_id["9"].name, "相关服务业")
        self.assertEqual(
            set(registry.template_by_id),
            {"software_ai", "semiconductor_design", "advanced_hardware", "new_materials", "biopharma", "medical_device"},
        )
        self.assertEqual(
            {item.number for item in registry.standards.references},
            {"GB/T 37264-2018", "GB/T 40518-2021", "ISO 16290:2013"},
        )
        self.assertTrue(all("未收录标准正文" in item.use_boundary for item in registry.standards.references))
        self.assertEqual(registry.templates.registry_version, "technology-templates.v2")
        self.assertTrue(all(item.selection_terms for item in registry.templates.templates))

    async def test_fact_type_registry_covers_all_template_references(self):
        registry = KnowledgeSemanticRegistry()
        registered = registry.allowed_technology_fact_types
        referenced = set()
        for template in registry.templates.templates:
            referenced.update(item.casefold() for item in template.evidence_types)
            for milestone in template.milestones:
                referenced.update(item.casefold() for item in milestone.fact_type_hints)
            for rule in template.inference_rules:
                referenced.update(item.casefold() for item in rule.fact_type_any)
        self.assertTrue(referenced.issubset(registered), sorted(referenced - registered))
        self.assertEqual(registry.fact_types.registry_version, "technology-fact-types.v1")

    async def test_extractor_rejects_unregistered_business_types_and_accepts_registered_tech_types(self):
        company, chunks, sources, versions, registry, domain, selection = await self.extractor_inputs(1)
        illegal = [
            "financial_revenue", "financial_profit", "revenue", "order", "customer_order",
            "construction_project", "company_establishment", "corporate_info", "shareholding",
            "company_history", "company_registration", "award_nomination",
        ]
        valid = [
            " TAPEOUT ", "silicon_validation", "technical_specification", "customer_validation",
            "product_launch", "patent_grant",
        ]
        payload = {"facts": [], "information_gaps": []}
        for index, fact_type in enumerate(illegal + valid):
            payload["facts"].append({
                "source_chunk_id": chunks[0].chunk_id,
                "subject": f"事实对象{index}",
                "predicate": "披露",
                "object_value": f"合成事实{index}",
                "fact_type": fact_type,
                "event_time": None,
                "quantitative_value": None,
                "quantitative_unit": None,
                "domain_tags": ["1"],
                "template_tags": ["software_ai"],
            })
        model = FakeStructuredModel([payload])
        extractor = TechnologyFactExtractor(model, registry, processor_version="test.v1")
        facts, _, _, _, report = await extractor.extract(
            company_id=company.company_id, domain_profile=domain, selection=selection,
            chunks=chunks, sources=sources, versions=versions,
        )
        self.assertEqual({fact.fact_type for fact in facts}, {
            "tapeout", "silicon_validation", "technical_specification",
            "customer_validation", "product_launch", "patent_grant",
        })
        self.assertEqual(report.rejected_fact_type_count, len(illegal))
        self.assertEqual(report.rejected_fact_type_distribution, {
            key: 1 for key in sorted(illegal)
        })
        self.assertEqual(report.error_categories["unregistered_fact_type"], len(illegal))
        self.assertEqual(
            {item["id"] for item in model.calls[0]["payload"]["allowed_fact_types"]},
            registry.allowed_technology_fact_types,
        )

    async def test_processor_records_nontechnology_fact_type_warning(self):
        company, chunks = await self.add_general_chunk(
            "类型拒绝告警合成企业", "年报披露营业收入和公司设立信息。"
        )
        rejected = {
            "facts": [
                {
                    **fact_output(chunks[0].chunk_id)["facts"][0],
                    "fact_type": fact_type,
                }
                for fact_type in ("financial_revenue", "corporate_info", "company_establishment")
            ],
            "information_gaps": [],
        }
        model = FakeStructuredModel([
            classifier_result("E1"),
            rejected,
            {"observations": [], "information_gaps": []},
        ])
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(
            company.company_id
        )
        self.assertEqual(profile.technology_facts, [])
        self.assertEqual(profile.fact_extraction_report.rejected_fact_type_count, 3)
        self.assertEqual(
            profile.fact_extraction_report.rejected_fact_type_distribution,
            {"company_establishment": 1, "corporate_info": 1, "financial_revenue": 1},
        )
        self.assertIn("technology_fact_type_rejected:3", profile.processing_warnings)
        self.assertEqual(
            profile.technology_fact_type_registry_version, "technology-fact-types.v1"
        )

    async def test_ai_medical_imaging_can_select_software_and_device_together_and_preserve_trace(self):
        company_name = "合成智能影像有限公司"
        company, initial_chunks = await self.add_general_chunk(
            company_name,
            "该公司研发人工智能医学影像软件，并披露医疗器械影像分析系统已完成样机验证。",
        )
        original = [(item.chunk_id, item.text, item.citation.model_dump(mode="json")) for item in initial_chunks]
        chunk_id = initial_chunks[0].chunk_id
        model = FakeStructuredModel(
            [
                classifier_result("E1", templates=("software_ai", "medical_device")),
                fact_output(chunk_id, templates=("software_ai", "medical_device")),
            ]
        )
        processor = TechnologyKnowledgeProcessor(model, self.kb)
        # Interpreter references are request-local short refs assigned after extraction.
        async def dynamic_interpreter(prompt, payload):
            fact_ref = payload["technology_facts"][0]["fact_ref"]
            return interpreter_result(
                template_id="medical_device",
                milestone_id="prototype_validation",
                supporting_fact_refs=(fact_ref,),
            )

        original_complete = model.complete_json

        async def complete(prompt, payload):
            if "technology_facts" in payload:
                model.calls.append({"prompt": prompt, "payload": payload})
                return await dynamic_interpreter(prompt, payload)
            return await original_complete(prompt, payload)

        model.complete_json = complete
        profile = await processor.process_company(company.company_id)
        self.assertEqual(profile.template_selection.selected_template_ids, ["software_ai", "medical_device"])
        self.assertEqual(len(profile.technology_facts), 1)
        self.assertEqual(profile.technology_facts[0].source_chunk_id, chunk_id)
        expected_url = initial_chunks[0].citation.source_url
        self.assertEqual(profile.technology_facts[0].citation.source_url, expected_url)
        current_general = self.kb.repository.list_current_chunks(company.company_id, "general")
        self.assertEqual(
            [(item.chunk_id, item.text, item.citation.model_dump(mode="json")) for item in current_general],
            original,
        )
        derived = self.kb.repository.list_current_chunks(company.company_id, "lifecycle")
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0].metadata["semantic_kind"], "technology_fact")
        self.assertEqual(derived[0].metadata["derived_from_chunk_id"], chunk_id)
        self.assertEqual(derived[0].metadata["fact_id"], profile.technology_facts[0].fact_id)
        self.assertEqual(derived[0].citation.source_url, expected_url)
        self.assertEqual(derived[0].citation.excerpt, initial_chunks[0].citation.excerpt)
        found = await self.kb.search(
            KnowledgeSearchRequest(
                query="智能影像系统 临床验证样机测试",
                top_k=10,
                company_id=company.company_id,
                knowledge_layers=[KnowledgeLayer.LIFECYCLE],
            )
        )
        self.assertTrue(found)
        self.assertTrue(any(item.chunk.chunk_id == derived[0].chunk_id for item in found))
        self.assertEqual(found[0].source.canonical_url, expected_url)
        self.assertEqual(found[0].source.title, initial_chunks[0].citation.source_title)
        self.assertEqual(found[0].chunk.citation.excerpt, initial_chunks[0].citation.excerpt)
        self.assertEqual(found[0].chunk.citation.locator.url, expected_url)
        persisted = self.kb.repository.get_technology_semantic_profile(company.company_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["profile_id"], profile.profile_id)

    async def test_new_materials_and_advanced_hardware_are_adaptively_composable(self):
        company_name = "合成新能源材料装备有限公司"
        company, chunks = await self.add_general_chunk(
            company_name,
            "企业建设新型储能材料百吨级中试线，并研制配套的先进热处理装备及控制系统。",
        )
        selected = ("new_materials", "advanced_hardware")
        model = FakeStructuredModel(
            [
                classifier_result("E1", domains=("3", "2"), templates=selected),
                fact_output(chunks[0].chunk_id, fact_type="pilot_line", templates=selected),
                {"observations": [], "information_gaps": []},
            ]
        )
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)
        self.assertEqual(set(profile.template_selection.selected_template_ids), set(selected))
        self.assertEqual(set(profile.domain_profile.primary_domains), {"3", "2"})

    async def test_processor_uses_same_bounded_diverse_evidence_for_classifier_and_extractor(self):
        company_name = "语义证据选择合成企业"
        company = None
        for index in range(10):
            company, _ = await self.add_general_chunk(
                company_name,
                f"来源{index}披露了芯片架构、产品研发和验证进展。" + (f"独立技术细节{index} " * 80),
                url_suffix=f"source-{index}",
            )
        before = self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.GENERAL.value)

        class CapturingModel:
            def __init__(self):
                self.calls = []

            async def complete_json(self, prompt, payload):
                self.calls.append({"prompt": prompt, "payload": payload})
                if "general_chunks" in payload and "selected_templates" not in payload:
                    evidence_ref = payload["general_chunks"][0]["evidence_ref"]
                    return classifier_result(evidence_ref)
                if "general_chunks" in payload and "selected_templates" in payload:
                    first_id = payload["general_chunks"][0]["chunk_id"]
                    return fact_output(first_id)
                fact_ref = payload["technology_facts"][0]["fact_ref"]
                return interpreter_result(supporting_fact_refs=(fact_ref,))

        model = CapturingModel()
        processor = TechnologyKnowledgeProcessor(model, self.kb)
        profile = await processor.process_company(company.company_id)
        classifier_chunks = model.calls[0]["payload"]["general_chunks"]
        extraction_calls = [
            call for call in model.calls
            if "selected_templates" in call["payload"]
            and "general_chunks" in call["payload"]
        ]
        extractor_chunks = [
            chunk
            for call in extraction_calls
            for chunk in call["payload"]["general_chunks"]
        ]
        classifier_ids = [item["evidence_ref"] for item in classifier_chunks]
        extractor_ids = [item["chunk_id"] for item in extractor_chunks]
        classifier_chars = sum(len(item["text"]) for item in classifier_chunks)
        extractor_chars = sum(len(item["text"]) for item in extractor_chunks)

        self.assertEqual(classifier_ids, [f"E{index}" for index in range(1, len(classifier_ids) + 1)])
        self.assertNotIn("chunk_id", classifier_chunks[0])
        self.assertNotIn("citation", classifier_chunks[0])
        self.assertLessEqual(classifier_chars, 48_000)
        self.assertLessEqual(extractor_chars, 80_000)
        self.assertEqual(profile.semantic_evidence_selection.selected_char_count, classifier_chars)
        self.assertEqual(profile.classifier_evidence_selection, profile.semantic_evidence_selection)
        self.assertEqual(profile.fact_extraction_report.input_chunk_count, len(extractor_ids))
        self.assertEqual(profile.fact_extraction_report.batch_count, len(extraction_calls))
        self.assertTrue(all(len(call["payload"]["general_chunks"]) <= 3 for call in extraction_calls))
        self.assertNotIn("citation", model.calls[0]["payload"]["general_chunks"][0])
        self.assertEqual(profile.classifier_processed_chunk_count, len(classifier_ids))
        self.assertEqual(profile.fact_processed_chunk_count, len(extractor_ids))
        self.assertEqual(profile.processed_general_chunk_count, len(classifier_ids))
        self.assertGreaterEqual(profile.semantic_evidence_selection.selected_source_count, 8)
        self.assertEqual(len(before), len(self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.GENERAL.value)))
        self.assertEqual(
            [(item.chunk_id, item.text) for item in before],
            [(item.chunk_id, item.text) for item in self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.GENERAL.value)],
        )
        persisted = self.kb.repository.get_technology_semantic_profile(company.company_id)
        self.assertEqual(persisted["semantic_evidence_selection"], profile.semantic_evidence_selection.model_dump(mode="json"))
        self.assertEqual(persisted["classifier_evidence_selection"], profile.classifier_evidence_selection.model_dump(mode="json"))
        self.assertEqual(persisted["fact_evidence_selection"], profile.fact_evidence_selection.model_dump(mode="json"))
        self.assertEqual(processor.last_execution_trace["semantic_stage"], "complete")
        self.assertEqual(processor.last_execution_trace["semantic_substage"], "complete")
        self.assertEqual(processor.last_execution_trace["interpreter_status"], "completed")
        self.assertEqual(processor.last_execution_trace["persistence_phase"], "complete")
        self.assertEqual(
            processor.last_execution_trace["derived_fact_chunk_count"],
            processor.last_execution_trace["derived_unique_chunk_id_count"],
        )
        self.assertIn("milestone_status_distribution", processor.last_execution_trace)
        self.assertIn("blocked_inference_count", processor.last_execution_trace)
        self.assertEqual(
            profile.interpreter_report.input_fact_count,
            len(profile.technology_facts),
        )
        self.assertEqual(
            profile.interpreter_report.output_observation_count,
            len(profile.milestone_observations),
        )
        self.assertEqual(
            processor.last_execution_trace["interpreter_invalid_fact_reference_count"],
            profile.interpreter_report.invalid_fact_reference_count,
        )
        self.assertEqual(
            processor.last_execution_trace["interpreter_downgraded_observation_count"],
            profile.interpreter_report.downgraded_observation_count,
        )

    async def test_fact_selected_chunk_outside_classifier_set_can_be_extracted_and_persisted(self):
        company_name = "模板相关事实回溯合成企业"
        company = company_for_name(company_name)
        opening = [
            f"企业登记与背景资料第{i}段，记载注册地址、组织信息和一般情况。"
            + (f"一般背景资料{i}，股本与登记信息仅用于合成测试。" * 12)
            for i in range(24)
        ]
        raw_text = "\n\n".join(opening + [
            "芯片完成流片并进行硅片验证，披露了回片后的芯片测试进展。"
        ])
        prepared = prepare_web_source(
            SearchResult(
                title="合成技术资料",
                url="https://long-report.example.test/disclosure",
                content=raw_text[:260],
                raw_content=raw_text,
                provider="test",
            ),
            enterprise_name=company_name,
            company=company,
        )
        await self.kb.upsert_source_version(
            prepared.source,
            prepared.source_version,
            prepared.chunks,
            company=company,
        )
        raw_chunks = self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.GENERAL.value)
        self.assertGreater(len(raw_chunks), 3)
        target = next(chunk for chunk in raw_chunks if "芯片完成流片" in chunk.text)

        class EvidenceAwareModel:
            def __init__(self):
                self.calls = []

            async def complete_json(self, _prompt, payload):
                self.calls.append(payload)
                if "selected_templates" not in payload:
                    return classifier_result("E1", templates=("semiconductor_design",))
                if "technology_facts" in payload:
                    return interpreter_result(
                        "semiconductor_design", "tapeout", supporting_fact_refs=("F1",)
                    )
                fact_chunk_id = payload["general_chunks"][0]["chunk_id"]
                return fact_output(
                    fact_chunk_id,
                    fact_type="tapeout",
                    templates=("semiconductor_design",),
                )

        model = EvidenceAwareModel()
        processor = TechnologyKnowledgeProcessor(model, self.kb)
        profile = await processor.process_company(company.company_id)
        classifier_ids = set(profile.classifier_evidence_selection.selected_chunk_ids)
        fact_ids = set(profile.fact_evidence_selection.selected_chunk_ids)

        self.assertNotIn(target.chunk_id, classifier_ids)
        self.assertIn(target.chunk_id, fact_ids)
        self.assertTrue(profile.technology_facts)
        fact = next(item for item in profile.technology_facts if item.source_chunk_id == target.chunk_id)
        self.assertEqual(fact.citation.source_url, target.citation.source_url)
        derived = self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.LIFECYCLE.value)
        self.assertTrue(any(item.metadata.get("derived_from_chunk_id") == target.chunk_id for item in derived))
        self.assertGreater(profile.processed_general_chunk_count, 0)

    async def test_processor_trace_identifies_classifier_timeout_and_keeps_selection_report(self):
        company, _ = await self.add_general_chunk(
            "语义分类超时企业", "合成技术披露用于验证语义阶段 trace。"
        )

        class TimingOutModel:
            async def complete_json(self, _prompt, _payload):
                raise TimeoutError("synthetic timeout")

        processor = TechnologyKnowledgeProcessor(TimingOutModel(), self.kb)
        with self.assertRaises(TimeoutError):
            await processor.process_company(company.company_id)

        self.assertEqual(processor.last_execution_trace["semantic_stage"], "classifier")
        self.assertEqual(processor.last_execution_trace["semantic_substage"], "domain_template_classifier")
        self.assertEqual(processor.last_execution_trace["selected_chunk_count"], 1)
        self.assertGreater(processor.last_execution_trace["selected_char_count"], 0)

    async def test_processor_trace_keeps_classifier_and_extractor_results_on_interpreter_timeout(self):
        company, chunks = await self.add_general_chunk(
            "解释器超时 trace 企业", "合成企业完成智能软件原型研发和性能测试。"
        )

        class InterpreterTimeoutModel:
            def __init__(self):
                self.calls = 0

            async def complete_json(self, _prompt, payload):
                self.calls += 1
                if self.calls == 1:
                    return classifier_result("E1")
                if self.calls == 2:
                    return fact_output(chunks[0].chunk_id)
                raise StructuredModelError("timeout", "synthetic timeout")

        processor = TechnologyKnowledgeProcessor(InterpreterTimeoutModel(), self.kb)
        with self.assertRaises(StructuredModelError):
            await processor.process_company(company.company_id)

        trace = processor.last_execution_trace
        self.assertEqual(trace["semantic_substage"], "technology_interpreter")
        self.assertEqual(trace["primary_domains"], ["1"])
        self.assertEqual(trace["secondary_domains"], [])
        self.assertEqual(trace["selected_template_ids"], ["software_ai"])
        self.assertEqual(trace["classifier_report"]["valid_domain_evidence_count"], 1)
        self.assertEqual(trace["technology_fact_count"], 1)
        self.assertEqual(trace["fact_type_distribution"], {"prototype": 1})
        self.assertEqual(trace["source_quality_distribution"], {"weak_web": 1})

    async def test_processor_trace_distinguishes_derived_and_profile_persistence_failures(self):
        for suffix, failure_target, expected_substage in (
            ("derived", "derived", "derived_chunk_persistence"),
            ("profile", "profile", "semantic_profile_persistence"),
        ):
            with self.subTest(failure_target=failure_target):
                company, chunks = await self.add_general_chunk(
                    f"持久化 trace 企业 {suffix}", "合成企业披露智能软件原型研发进展。",
                    url_suffix=f"persistence-{suffix}",
                )
                processor = TechnologyKnowledgeProcessor(
                    FakeStructuredModel(
                        [
                            classifier_result("E1"),
                            fact_output(chunks[0].chunk_id),
                            {"observations": [], "information_gaps": []},
                        ]
                    ),
                    self.kb,
                )
                if failure_target == "derived":
                    async def fail_derived(*_args, **_kwargs):
                        raise RuntimeError("synthetic derived persistence failure")
                    processor._write_derived_chunks = fail_derived
                else:
                    def fail_profile(_profile):
                        raise RuntimeError("synthetic profile persistence failure")
                    processor.repository.save_technology_semantic_profile = fail_profile

                with self.assertRaises(RuntimeError):
                    await processor.process_company(company.company_id)

                trace = processor.last_execution_trace
                self.assertEqual(trace["semantic_stage"], "persistence")
                self.assertEqual(trace["semantic_substage"], expected_substage)
                self.assertEqual(trace["interpreter_status"], "completed")
                self.assertEqual(trace["persistence_phase"], expected_substage)
                self.assertEqual(trace["milestone_observation_count"], 3)
                self.assertEqual(trace["milestone_status_distribution"], {"no_evidence": 3})
                self.assertEqual(trace["blocked_inference_count"], 0)
                self.assertEqual(trace["derived_fact_chunk_count"], 1)
                self.assertEqual(trace["derived_unique_chunk_id_count"], 1)

    async def test_unknown_company_without_general_chunks_is_not_guessed(self):
        company = company_for_name("未知合成企业")
        self.kb.repository.upsert_company(company)
        model = FakeStructuredModel([])
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)
        self.assertEqual(profile.domain_profile.status, "insufficient_evidence")
        self.assertEqual(profile.domain_profile.primary_domains, [])
        self.assertEqual(profile.template_selection.selected_template_ids, [])
        self.assertEqual(model.calls, [])

    async def test_classifier_rejects_registry_ids_and_chunk_ids_outside_inputs(self):
        company, chunks = await self.add_general_chunk("证据校验企业", "企业拥有一项创新技术及软件产品。")
        model = FakeStructuredModel(
            [classifier_result("E999", domains=("domain_invented",), templates=("made_up",))]
        )
        with self.assertRaises(StructuredModelError):
            await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)

    async def test_classifier_rejects_unknown_domain_and_template_ids(self):
        overlapping = classifier_result("E1")
        overlapping["secondary_domain_ids"] = ["1"]
        cases = [
            ("bad-domain", classifier_result("E1", domains=("unknown-domain",))),
            ("bad-template", classifier_result("E1", templates=("unknown-template",))),
            ("overlapping-domains", overlapping),
            ("duplicate-template", classifier_result("E1", templates=("software_ai", "software_ai"))),
        ]
        for suffix, bad_output in cases:
            with self.subTest(case=suffix):
                company, chunks = await self.add_general_chunk(
                    f"registry-ID校验-{suffix}", "企业披露了科技产品和研发路线。"
                )
                bad_output["domain_evidence_refs"] = ["E1"]
                bad_output["template_evidence"] = [
                    {
                        "template_id": bad_output["selected_template_ids"][0],
                        "evidence_refs": ["E1"],
                        "reason": "test",
                    }
                ]
                model = FakeStructuredModel([bad_output, bad_output])
                with self.assertRaises(StructuredModelError):
                    await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)

    async def test_classifier_uses_temporary_refs_and_maps_deduplicated_refs_to_stable_chunk_ids(self):
        company, first = await self.add_general_chunk("classifier引用映射企业", "企业研发芯片架构与软件平台。", url_suffix="one")
        _, second = await self.add_general_chunk("classifier引用映射企业", "企业披露产品验证进展。", url_suffix="two")
        chunks = [first[0], second[0]]
        output = classifier_result("E1")
        output["domain_evidence_refs"] = ["E1", "E1", "E2"]
        output["template_evidence"][0]["evidence_refs"] = ["E1", "E1", "E2"]
        model = FakeStructuredModel([output])
        classifier = TechnologyDomainClassifier(model, KnowledgeSemanticRegistry())
        domain, selection, _ = await classifier.classify(
            company.canonical_name,
            chunks,
            {chunk.chunk_id: {"category": "weak_web", "source_type": "web"} for chunk in chunks},
        )

        payload_chunks = model.calls[0]["payload"]["general_chunks"]
        self.assertEqual([item["evidence_ref"] for item in payload_chunks], ["E1", "E2"])
        self.assertNotIn("chunk_id", payload_chunks[0])
        self.assertNotIn("citation", payload_chunks[0])
        self.assertNotIn(chunks[0].chunk_id, str(payload_chunks))
        self.assertEqual(domain.evidence_chunk_ids, [chunks[0].chunk_id, chunks[1].chunk_id])
        self.assertEqual(selection.evidence[0].evidence_chunk_ids, [chunks[0].chunk_id, chunks[1].chunk_id])
        self.assertEqual(classifier.last_report.duplicate_evidence_reference_count, 2)

    async def test_classifier_invalid_domain_refs_fail_closed_or_keep_valid_subset(self):
        company, chunks = await self.add_general_chunk("classifier非法引用企业", "企业披露芯片研发。")
        quality = {chunks[0].chunk_id: {"category": "weak_web", "source_type": "web"}}

        all_invalid = classifier_result("E999")
        all_invalid["domain_evidence_refs"] = ["E999"]
        classifier = TechnologyDomainClassifier(FakeStructuredModel([all_invalid]), KnowledgeSemanticRegistry())
        domain, _, _ = await classifier.classify(company.canonical_name, chunks, quality)
        self.assertEqual(domain.status, "insufficient_evidence")
        self.assertEqual(domain.primary_domains, [])
        self.assertEqual(domain.evidence_chunk_ids, [])
        self.assertTrue(classifier.last_report.downgraded_domain_classification)
        self.assertEqual(classifier.last_report.invalid_domain_evidence_reference_count, 1)

        partial = classifier_result("E1")
        partial["domain_evidence_refs"] = ["E1", "E999"]
        classifier = TechnologyDomainClassifier(FakeStructuredModel([partial]), KnowledgeSemanticRegistry())
        domain, _, _ = await classifier.classify(company.canonical_name, chunks, quality)
        self.assertEqual(domain.status, "classified")
        self.assertEqual(domain.evidence_chunk_ids, [chunks[0].chunk_id])
        self.assertFalse(classifier.last_report.downgraded_domain_classification)
        self.assertEqual(classifier.last_report.invalid_domain_evidence_reference_count, 1)

    async def test_classifier_drops_only_templates_without_valid_evidence(self):
        company, chunks = await self.add_general_chunk("classifier模板引用企业", "企业披露芯片研发及软件产品。")
        output = classifier_result("E1", templates=("software_ai", "advanced_hardware"))
        output["template_evidence"][1]["evidence_refs"] = ["E999"]
        classifier = TechnologyDomainClassifier(FakeStructuredModel([output]), KnowledgeSemanticRegistry())
        domain, selection, _ = await classifier.classify(
            company.canonical_name,
            chunks,
            {chunks[0].chunk_id: {"category": "weak_web", "source_type": "web"}},
        )
        self.assertEqual(domain.status, "classified")
        self.assertEqual(selection.selected_template_ids, ["software_ai"])
        self.assertEqual(classifier.last_report.dropped_template_count, 1)
        self.assertEqual(classifier.last_report.selected_template_count_before_validation, 2)
        self.assertEqual(classifier.last_report.selected_template_count_after_validation, 1)

    async def test_unknown_extractor_source_chunk_discards_fact_and_records_warning(self):
        company, chunks = await self.add_general_chunk("错误引用企业", "企业公开发布了一项技术论文和软件产品。")
        bad_fact = fact_output("forged-chunk")
        model = FakeStructuredModel(
            [
                classifier_result("E1"),
                bad_fact,
                {"observations": [], "information_gaps": []},
            ]
        )
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)
        self.assertEqual(profile.technology_facts, [])
        self.assertTrue(any("forged-chunk" in warning for warning in profile.processing_warnings))

    async def test_reprocessing_keeps_fact_and_derived_chunk_ids_stable_and_reloads_profile(self):
        company, chunks = await self.add_general_chunk("稳定事实企业", "企业完成人工智能软件原型系统开发与功能验证。")
        chunk_id = chunks[0].chunk_id

        def responses():
            return [
                classifier_result("E1"),
                fact_output(chunk_id),
                {"observations": [], "information_gaps": []},
            ]

        processor = TechnologyKnowledgeProcessor(FakeStructuredModel(responses()), self.kb)
        first = await processor.process_company(company.company_id)
        derived_first = self.kb.repository.list_current_chunks(company.company_id, "lifecycle")
        chroma_count_after_first = self.kb._collection.count()
        second = await TechnologyKnowledgeProcessor(FakeStructuredModel(responses()), self.kb).process_company(company.company_id)
        derived_second = self.kb.repository.list_current_chunks(company.company_id, "lifecycle")
        self.assertEqual(first.technology_facts[0].fact_id, second.technology_facts[0].fact_id)
        self.assertEqual([item.chunk_id for item in derived_first], [item.chunk_id for item in derived_second])
        self.assertEqual(
            derived_first[0].chunk_id,
            derived_fact_chunk_id_for(
                derived_first[0].source_version_id, first.technology_facts[0].fact_id
            ),
        )
        self.assertEqual(self.kb._collection.count(), chroma_count_after_first)
        self.assertEqual(self.kb.repository.get_technology_semantic_profile(company.company_id)["profile_id"], first.profile_id)

    async def test_same_spo_with_different_fact_types_persists_two_derived_chunks(self):
        company, chunks = await self.add_general_chunk(
            "派生事实身份碰撞企业", "思元370完成产品发布，公开资料用于合成回归测试。"
        )
        raw = chunks[0]
        two_facts = fact_output(raw.chunk_id)
        fact_a = two_facts["facts"][0]
        fact_a.update(
            subject="思元370",
            predicate="完成",
            object_value="产品发布",
            fact_type="product_launch",
        )
        fact_b = dict(fact_a, fact_type="customer_validation")
        two_facts["facts"] = [fact_a, fact_b]
        before_count = self.kb._collection.count()
        processor = TechnologyKnowledgeProcessor(
            FakeStructuredModel(
                [
                    classifier_result("E1"),
                    two_facts,
                    {"observations": [], "information_gaps": []},
                ]
            ),
            self.kb,
        )

        profile = await processor.process_company(company.company_id)

        derived = [
            chunk
            for chunk in self.kb.repository.list_chunks_for_version(raw.source_version_id)
            if chunk.metadata.get("semantic_kind") == "technology_fact"
        ]
        self.assertEqual(len(profile.technology_facts), 2)
        self.assertEqual(profile.technology_facts[0].citation.locator, profile.technology_facts[1].citation.locator)
        self.assertEqual(
            (profile.technology_facts[0].subject, profile.technology_facts[0].predicate, profile.technology_facts[0].object_value),
            (profile.technology_facts[1].subject, profile.technology_facts[1].predicate, profile.technology_facts[1].object_value),
        )
        self.assertNotEqual(profile.technology_facts[0].fact_id, profile.technology_facts[1].fact_id)
        self.assertEqual(len(derived), 2)
        self.assertEqual(len({item.chunk_id for item in derived}), 2)
        self.assertEqual(
            {item.chunk_id for item in derived},
            {
                derived_fact_chunk_id_for(raw.source_version_id, fact.fact_id)
                for fact in profile.technology_facts
            },
        )
        self.assertEqual(self.kb._collection.count(), before_count + 2)
        self.assertEqual(processor.last_execution_trace["derived_fact_chunk_count"], 2)
        self.assertEqual(processor.last_execution_trace["derived_unique_chunk_id_count"], 2)

    async def test_snippet_sources_are_weak_and_cannot_support_a_strong_milestone(self):
        company, strong_chunks = await self.add_general_chunk("摘要证据企业", "企业官网产品说明包含人工智能软件技术路线和产品信息。")
        _, snippet_chunks = await self.add_general_chunk("摘要证据企业", "公司在摘要中称已完成某人工智能模型 benchmark。", snippet=True, url_suffix="search-snippet")
        chunks = [*strong_chunks, *snippet_chunks]
        model = FakeStructuredModel(
            [
                classifier_result("E1"),
                fact_output(snippet_chunks[0].chunk_id, fact_type="benchmark"),
                None,
            ]
        )
        async def dynamic(prompt, payload):
            fact_ref = payload["technology_facts"][0]["fact_ref"]
            return interpreter_result(supporting_fact_refs=(fact_ref,))

        async def complete(prompt, payload):
            if "technology_facts" in payload:
                return await dynamic(prompt, payload)
            return await model.complete_json_original(prompt, payload)

        model.complete_json_original = model.complete_json
        model.complete_json = complete
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)
        self.assertEqual(profile.technology_facts[0].source_quality.category, "snippet_only")
        observation = next(item for item in profile.milestone_observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.status, "limited_support")

    async def test_forbidden_inferences_are_filtered_for_required_templates(self):
        cases = [
            ("semiconductor_design", "tapeout", "tapeout", "volume_production", "已规模量产且良率稳定"),
            ("software_ai", "benchmark_evaluation", "benchmark", "production_deployment", "模型已在生产环境部署并大规模商用"),
            ("new_materials", "pilot_line", "pilot_line", "scale_production", "该材料已规模化量产并稳定供货"),
            ("biopharma", "phase_ii", "phase_ii", "marketing_approval", "药物已完成III期并获批上市"),
            ("medical_device", "registration", "registration_certificate", "clinical_adoption", "产品已被市场广泛接受并稳定销售"),
        ]
        for template_id, milestone_id, fact_type, claimed_milestone, forbidden_reason in cases:
            with self.subTest(template=template_id):
                company, chunks = await self.add_general_chunk(
                    f"边界测试企业-{template_id}", "公开资料披露一项科技研发事件和相关测试记录。"
                )
                raw = chunks[0]
                source = self.kb.repository.get_source(raw.source_id)
                version = self.kb.repository.get_source_version(raw.source_version_id)
                quality = SourceQuality(
                    category="third_party",
                    source_type=source.source_type.value,
                    content_scope="full_content",
                    rationale="fixture",
                )
                fact = TechnologyFact(
                    fact_id=f"fact-{template_id}",
                    company_id=company.company_id,
                    subject="技术产品",
                    predicate="披露",
                    object_value="研发事件",
                    fact_type=fact_type,
                    source_chunk_id=raw.chunk_id,
                    citation=raw.citation,
                    template_tags=[template_id],
                    source_quality=quality,
                    processor_version="technology-semantic.v1",
                )
                selection = TechnologyTemplateSelection(
                    status="selected",
                    selected_template_ids=[template_id],
                    evidence=[{"template_id": template_id, "evidence_chunk_ids": [raw.chunk_id], "reason": "evidence"}],
                    reason="selected",
                    registry_version="technology-templates.v1",
                )
                model = FakeStructuredModel(
                    [
                        interpreter_result(
                            template_id,
                            claimed_milestone,
                            status="supported",
                            reason=forbidden_reason,
                            supporting_fact_refs=("F1",),
                        )
                    ]
                )
                observations, _ = await TechnologyInterpreter(model, KnowledgeSemanticRegistry()).interpret(
                    selection, [fact]
                )
                observation = next(item for item in observations if item.milestone_id == claimed_milestone)
                self.assertEqual(observation.status, "limited_support")
                self.assertTrue(observation.blocked_inferences)
                self.assertNotEqual(observation.reason, forbidden_reason)
                self.assertTrue(observation.supporting_fact_ids)

    async def interpreter_fact_fixtures(self):
        company, chunks = await self.add_general_chunk(
            "短事实引用回归企业", "合成资料披露芯片产品研发和验证。"
        )
        raw = chunks[0]
        source = self.kb.repository.get_source(raw.source_id)
        quality = SourceQuality(
            category="third_party",
            source_type=source.source_type.value,
            content_scope="full_content",
            rationale="fixture",
        )
        facts = [
            TechnologyFact(
                fact_id="tf_aaaa0000000000000000000000000000",
                company_id=company.company_id,
                subject="思元370",
                predicate="发布",
                object_value="芯片产品",
                fact_type="product_launch",
                source_chunk_id=raw.chunk_id,
                citation=raw.citation,
                template_tags=["software_ai"],
                source_quality=quality,
                processor_version="technology-semantic.v5",
            ),
            TechnologyFact(
                fact_id="tf_bbbb0000000000000000000000000000",
                company_id=company.company_id,
                subject="思元370",
                predicate="完成",
                object_value="产品验证",
                fact_type="customer_validation",
                source_chunk_id=raw.chunk_id,
                citation=raw.citation,
                template_tags=["software_ai"],
                source_quality=quality,
                processor_version="technology-semantic.v5",
            ),
        ]
        selection = TechnologyTemplateSelection(
            status="selected",
            selected_template_ids=["software_ai"],
            evidence=[{"template_id":"software_ai","evidence_chunk_ids":[raw.chunk_id],"reason":"evidence"}],
            reason="selected",
            registry_version="technology-templates.v1",
        )
        return facts, selection

    async def test_interpreter_sends_compact_fact_refs_and_maps_f1_to_real_fact_id(self):
        facts, selection = await self.interpreter_fact_fixtures()
        model = FakeStructuredModel(
            [interpreter_result(supporting_fact_refs=("F1",))]
        )
        interpreter = TechnologyInterpreter(model, KnowledgeSemanticRegistry())

        observations, _ = await interpreter.interpret(selection, facts)

        payload = model.calls[0]["payload"]["technology_facts"]
        self.assertEqual([item["fact_ref"] for item in payload], ["F1", "F2"])
        self.assertEqual(set(payload[0]), {
            "fact_ref", "subject", "predicate", "object_value", "fact_type",
            "event_time", "quantitative_value", "quantitative_unit", "domain_tags",
            "template_tags", "source_quality",
        })
        serialized_payload = str(model.calls[0]["payload"])
        for forbidden in (
            facts[0].fact_id, facts[1].fact_id, "company_id", "source_chunk_id",
            "citation", "processor_version", "source_url", "locator", "rationale",
        ):
            self.assertNotIn(forbidden, serialized_payload)
        observation = next(item for item in observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.supporting_fact_ids, [facts[0].fact_id])
        self.assertEqual(interpreter.last_report.input_fact_count, 2)
        self.assertEqual(interpreter.last_report.model_observation_count, 1)

    async def test_unknown_f99_reference_fails_closed_without_raising(self):
        facts, selection = await self.interpreter_fact_fixtures()
        interpreter = TechnologyInterpreter(
            FakeStructuredModel([interpreter_result(supporting_fact_refs=("F99",))]),
            KnowledgeSemanticRegistry(),
        )
        observations, gaps = await interpreter.interpret(selection, facts)
        observation = next(item for item in observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.status, "no_evidence")
        self.assertEqual(observation.supporting_fact_ids, [])
        self.assertEqual(interpreter.last_report.invalid_fact_reference_count, 1)
        self.assertEqual(interpreter.last_report.fully_invalid_observation_count, 1)
        self.assertEqual(interpreter.last_report.downgraded_observation_count, 1)
        self.assertIn("invalid_fact_reference:software_ai/research_prototype", gaps)

    async def test_partial_valid_reference_is_retained_and_downgraded(self):
        facts, selection = await self.interpreter_fact_fixtures()
        interpreter = TechnologyInterpreter(
            FakeStructuredModel([
                interpreter_result(supporting_fact_refs=("F1", "F99"))
            ]),
            KnowledgeSemanticRegistry(),
        )
        observations, _ = await interpreter.interpret(selection, facts)
        observation = next(item for item in observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.status, "limited_support")
        self.assertEqual(observation.supporting_fact_ids, [facts[0].fact_id])
        self.assertEqual(interpreter.last_report.partially_invalid_observation_count, 1)
        self.assertEqual(interpreter.last_report.downgraded_observation_count, 1)

    async def test_conflict_requires_valid_fact_references_on_both_sides(self):
        facts, selection = await self.interpreter_fact_fixtures()
        model = FakeStructuredModel([{
            "observations": [
                {
                    "milestone_id":"research_prototype", "template_id":"software_ai",
                    "status":"conflict", "supporting_fact_refs":["F1"],
                    "contradicting_fact_refs":["F2"], "reason":"存在相反披露。",
                },
                {
                    "milestone_id":"benchmark_evaluation", "template_id":"software_ai",
                    "status":"conflict", "supporting_fact_refs":["F1"],
                    "contradicting_fact_refs":["F99"], "reason":"反证引用待核验。",
                },
            ], "information_gaps": [],
        }])
        interpreter = TechnologyInterpreter(model, KnowledgeSemanticRegistry())
        observations, _ = await interpreter.interpret(selection, facts)
        by_milestone = {item.milestone_id:item for item in observations}
        self.assertEqual(by_milestone["research_prototype"].status, "conflict")
        self.assertEqual(by_milestone["research_prototype"].supporting_fact_ids, [facts[0].fact_id])
        self.assertEqual(by_milestone["research_prototype"].contradicting_fact_ids, [facts[1].fact_id])
        self.assertEqual(by_milestone["benchmark_evaluation"].status, "limited_support")
        self.assertEqual(by_milestone["benchmark_evaluation"].supporting_fact_ids, [facts[0].fact_id])
        self.assertEqual(by_milestone["benchmark_evaluation"].contradicting_fact_ids, [])
        self.assertEqual(interpreter.last_report.invalid_fact_reference_count, 1)

    async def test_duplicate_and_overlapping_refs_are_deduplicated_and_removed(self):
        facts, selection = await self.interpreter_fact_fixtures()
        model = FakeStructuredModel([{
            "observations": [{
                "milestone_id":"research_prototype", "template_id":"software_ai",
                "status":"conflict", "supporting_fact_refs":["F1","F1","F2"],
                "contradicting_fact_refs":["F2"], "reason":"引用存在重复与重叠。",
            }], "information_gaps": [],
        }])
        interpreter = TechnologyInterpreter(model, KnowledgeSemanticRegistry())
        observations, gaps = await interpreter.interpret(selection, facts)
        observation = next(item for item in observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.status, "limited_support")
        self.assertEqual(observation.supporting_fact_ids, [facts[0].fact_id])
        self.assertEqual(observation.contradicting_fact_ids, [])
        self.assertEqual(interpreter.last_report.duplicate_fact_reference_count, 1)
        self.assertEqual(interpreter.last_report.overlapping_reference_count, 1)
        self.assertEqual(interpreter.last_report.downgraded_observation_count, 1)
        self.assertIn("overlapping_fact_reference:software_ai/research_prototype", gaps)

    async def test_no_evidence_clears_attached_refs_and_keeps_status(self):
        facts, selection = await self.interpreter_fact_fixtures()
        interpreter = TechnologyInterpreter(
            FakeStructuredModel([interpreter_result(
                status="no_evidence", supporting_fact_refs=("F99",)
            )]),
            KnowledgeSemanticRegistry(),
        )
        observations, _ = await interpreter.interpret(selection, facts)
        observation = next(item for item in observations if item.milestone_id == "research_prototype")
        self.assertEqual(observation.status, "no_evidence")
        self.assertEqual(observation.supporting_fact_ids, [])
        self.assertEqual(interpreter.last_report.invalid_fact_reference_count, 1)
        self.assertEqual(interpreter.last_report.downgraded_observation_count, 0)

    async def test_interpreter_unknown_template_and_milestone_remain_strict(self):
        selection = TechnologyTemplateSelection(
            status="selected",
            selected_template_ids=["software_ai"],
            evidence=[{"template_id": "software_ai", "evidence_chunk_ids": ["chunk"], "reason": "evidence"}],
            reason="selected",
            registry_version="technology-templates.v1",
        )
        model = FakeStructuredModel([interpreter_result(supporting_fact_refs=("F99",))])
        interpreter = TechnologyInterpreter(model, KnowledgeSemanticRegistry())
        observations, _ = await interpreter.interpret(selection, [])
        self.assertTrue(observations)
        self.assertEqual(interpreter.last_report.invalid_fact_reference_count, 1)

        unknown_template = FakeStructuredModel([interpreter_result(template_id="unknown")])
        with self.assertRaises(StructuredModelError):
            await TechnologyInterpreter(unknown_template, KnowledgeSemanticRegistry()).interpret(selection, [])
        unknown_milestone = FakeStructuredModel([interpreter_result(milestone_id="unknown")])
        with self.assertRaises(StructuredModelError):
            await TechnologyInterpreter(unknown_milestone, KnowledgeSemanticRegistry()).interpret(selection, [])
        duplicate_observation = FakeStructuredModel([{
            "observations": [
                interpreter_result(status="no_evidence")["observations"][0],
                interpreter_result(status="no_evidence")["observations"][0],
            ],
            "information_gaps": [],
        }])
        with self.assertRaises(StructuredModelError) as raised:
            await TechnologyInterpreter(duplicate_observation, KnowledgeSemanticRegistry()).interpret(
                selection, []
            )
        self.assertEqual(raised.exception.category, "invalid_registry_reference")

    async def test_processor_persists_interpreter_reference_report_and_trace(self):
        company, chunks = await self.add_general_chunk(
            "非法短引用 trace 企业", "合成企业披露软件原型研发与测试。"
        )
        processor = TechnologyKnowledgeProcessor(
            FakeStructuredModel([
                classifier_result("E1"),
                fact_output(chunks[0].chunk_id),
                interpreter_result(supporting_fact_refs=("F99",)),
            ]),
            self.kb,
        )
        profile = await processor.process_company(company.company_id)
        observation = next(
            item for item in profile.milestone_observations
            if item.milestone_id == "research_prototype"
        )
        persisted = self.kb.repository.get_technology_semantic_profile(company.company_id)
        self.assertEqual(observation.status, "no_evidence")
        self.assertEqual(profile.interpreter_report.input_fact_count, 1)
        self.assertEqual(profile.interpreter_report.model_observation_count, 1)
        self.assertEqual(profile.interpreter_report.invalid_fact_reference_count, 1)
        self.assertEqual(profile.interpreter_report.downgraded_observation_count, 1)
        self.assertEqual(processor.last_execution_trace["interpreter_status"], "completed")
        self.assertEqual(processor.last_execution_trace["interpreter_invalid_fact_reference_count"], 1)
        self.assertEqual(processor.last_execution_trace["interpreter_downgraded_observation_count"], 1)
        self.assertEqual(profile.classifier_report.input_evidence_count, 1)
        self.assertEqual(profile.classifier_report.valid_domain_evidence_count, 1)
        self.assertEqual(persisted["classifier_report"]["input_evidence_count"], 1)
        self.assertEqual(persisted["interpreter_report"]["invalid_fact_reference_count"], 1)

    async def test_interpreter_cannot_mark_supported_without_fact_ids_and_keeps_explicit_negative_guard(self):
        company, chunks = await self.add_general_chunk("规则边界回归企业", "公开材料称研发团队完成一项芯片首次流片。")
        raw = chunks[0]
        source = self.kb.repository.get_source(raw.source_id)
        version = self.kb.repository.get_source_version(raw.source_version_id)
        fact = TechnologyFact(
            fact_id="tapeout-fact",
            company_id=company.company_id,
            subject="芯片",
            predicate="完成",
            object_value="首次流片",
            fact_type="tapeout",
            source_chunk_id=raw.chunk_id,
            citation=raw.citation,
            template_tags=["semiconductor_design"],
            source_quality=SourceQuality(
                category="third_party",
                source_type=source.source_type.value,
                content_scope="full_content",
                rationale="fixture",
            ),
            processor_version="technology-semantic.v1",
        )
        selection = TechnologyTemplateSelection(
            status="selected",
            selected_template_ids=["semiconductor_design"],
            evidence=[{"template_id": "semiconductor_design", "evidence_chunk_ids": [raw.chunk_id], "reason": "evidence"}],
            reason="selected",
            registry_version="technology-templates.v1",
        )
        model = FakeStructuredModel(
            [
                interpreter_result(
                    "semiconductor_design",
                    "tapeout",
                    reason="观察到流片，当前证据不能证明已量产。",
                    supporting_fact_refs=("F1",),
                )
            ]
        )
        observations, _ = await TechnologyInterpreter(model, KnowledgeSemanticRegistry()).interpret(selection, [fact])
        tapeout = next(item for item in observations if item.milestone_id == "tapeout")
        self.assertEqual(tapeout.status, "supported")
        self.assertTrue(tapeout.blocked_inferences)
        self.assertIn("不能证明已量产", tapeout.reason)

        no_fact_model = FakeStructuredModel(
            [interpreter_result("semiconductor_design", "tapeout", supporting_fact_refs=())]
        )
        no_fact_observations, _ = await TechnologyInterpreter(
            no_fact_model, KnowledgeSemanticRegistry()
        ).interpret(selection, [fact])
        no_fact_tapeout = next(item for item in no_fact_observations if item.milestone_id == "tapeout")
        self.assertEqual(no_fact_tapeout.status, "limited_support")


if __name__ == "__main__":
    unittest.main()
