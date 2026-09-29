"""Offline contract and pipeline tests for adaptive technology semantics."""

from __future__ import annotations

import sys
import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.contracts import KnowledgeLayer
from app.knowledge.identity import company_for_name
from app.knowledge.semantic.contracts import (
    MilestoneObservation,
    SourceQuality,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.interpreter import TechnologyInterpreter
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


def classifier_result(chunk_id, *, domains=("1",), templates=("software_ai",), status="classified"):
    return {
        "status": status,
        "primary_domain_ids": list(domains),
        "secondary_domain_ids": [],
        "domain_evidence_chunk_ids": [chunk_id] if status == "classified" else [],
        "domain_reason": "来源正文描述了与分类相符的技术产品。" if status == "classified" else "缺少可核验技术证据。",
        "selected_template_ids": list(templates),
        "template_evidence": [
            {"template_id": item, "evidence_chunk_ids": [chunk_id], "reason": "正文描述了相应研发对象。"}
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


def interpreter_result(template_id="software_ai", milestone_id="research_prototype", *, status="supported", reason="输入事实支持观察到该里程碑。", supporting_fact_ids=()):
    return {
        "observations": [
            {
                "milestone_id": milestone_id,
                "template_id": template_id,
                "status": status,
                "supporting_fact_ids": list(supporting_fact_ids),
                "contradicting_fact_ids": [],
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
                classifier_result(chunk_id, templates=("software_ai", "medical_device")),
                fact_output(chunk_id, templates=("software_ai", "medical_device")),
                interpreter_result(supporting_fact_ids=("fact-id-placeholder",)),
            ]
        )
        # Interpreter IDs are bound to the extractor's deterministic fact ID after the extraction call.
        model.responses.pop()
        model.responses.append(None)
        processor = TechnologyKnowledgeProcessor(model, self.kb)
        # Obtain the stable fact ID using a first extraction pass is unnecessary here: the interpreter
        # receives a concrete ID only after extraction, so use a model that derives it from the payload.
        async def dynamic_interpreter(prompt, payload):
            fact_id = payload["technology_facts"][0]["fact_id"]
            return interpreter_result(
                template_id="medical_device",
                milestone_id="prototype_validation",
                supporting_fact_ids=(fact_id,),
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
                classifier_result(chunks[0].chunk_id, domains=("3", "2"), templates=selected),
                fact_output(chunks[0].chunk_id, fact_type="pilot_line", templates=selected),
                {"observations": [], "information_gaps": []},
            ]
        )
        profile = await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)
        self.assertEqual(set(profile.template_selection.selected_template_ids), set(selected))
        self.assertEqual(set(profile.domain_profile.primary_domains), {"3", "2"})

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
            [classifier_result("forged-chunk", domains=("domain_invented",), templates=("made_up",))]
        )
        with self.assertRaises(StructuredModelError):
            await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)

    async def test_classifier_rejects_unknown_domain_and_template_ids(self):
        cases = [
            ("bad-domain", classifier_result("", domains=("unknown-domain",))),
            ("bad-template", classifier_result("", templates=("unknown-template",))),
        ]
        for suffix, bad_output in cases:
            with self.subTest(case=suffix):
                company, chunks = await self.add_general_chunk(
                    f"registry-ID校验-{suffix}", "企业披露了科技产品和研发路线。"
                )
                bad_output["domain_evidence_chunk_ids"] = [chunks[0].chunk_id]
                bad_output["template_evidence"] = [
                    {
                        "template_id": bad_output["selected_template_ids"][0],
                        "evidence_chunk_ids": [chunks[0].chunk_id],
                        "reason": "test",
                    }
                ]
                model = FakeStructuredModel([bad_output])
                with self.assertRaises(StructuredModelError):
                    await TechnologyKnowledgeProcessor(model, self.kb).process_company(company.company_id)

    async def test_unknown_extractor_source_chunk_discards_fact_and_records_warning(self):
        company, chunks = await self.add_general_chunk("错误引用企业", "企业公开发布了一项技术论文和软件产品。")
        bad_fact = fact_output("forged-chunk")
        model = FakeStructuredModel(
            [
                classifier_result(chunks[0].chunk_id),
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
                classifier_result(chunk_id),
                fact_output(chunk_id),
                {"observations": [], "information_gaps": []},
            ]

        processor = TechnologyKnowledgeProcessor(FakeStructuredModel(responses()), self.kb)
        first = await processor.process_company(company.company_id)
        derived_first = self.kb.repository.list_current_chunks(company.company_id, "lifecycle")
        second = await TechnologyKnowledgeProcessor(FakeStructuredModel(responses()), self.kb).process_company(company.company_id)
        derived_second = self.kb.repository.list_current_chunks(company.company_id, "lifecycle")
        self.assertEqual(first.technology_facts[0].fact_id, second.technology_facts[0].fact_id)
        self.assertEqual([item.chunk_id for item in derived_first], [item.chunk_id for item in derived_second])
        self.assertEqual(self.kb.repository.get_technology_semantic_profile(company.company_id)["profile_id"], first.profile_id)

    async def test_snippet_sources_are_weak_and_cannot_support_a_strong_milestone(self):
        company, strong_chunks = await self.add_general_chunk("摘要证据企业", "企业官网产品说明包含人工智能软件技术路线和产品信息。")
        _, snippet_chunks = await self.add_general_chunk("摘要证据企业", "公司在摘要中称已完成某人工智能模型 benchmark。", snippet=True, url_suffix="search-snippet")
        chunks = [*strong_chunks, *snippet_chunks]
        model = FakeStructuredModel(
            [
                classifier_result(strong_chunks[0].chunk_id),
                fact_output(snippet_chunks[0].chunk_id, fact_type="benchmark"),
                None,
            ]
        )
        async def dynamic(prompt, payload):
            fact_id = payload["technology_facts"][0]["fact_id"]
            return interpreter_result(supporting_fact_ids=(fact_id,))

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
                            supporting_fact_ids=(fact.fact_id,),
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

    async def test_interpreter_rejects_fact_ids_outside_fact_context(self):
        selection = TechnologyTemplateSelection(
            status="selected",
            selected_template_ids=["software_ai"],
            evidence=[{"template_id": "software_ai", "evidence_chunk_ids": ["chunk"], "reason": "evidence"}],
            reason="selected",
            registry_version="technology-templates.v1",
        )
        model = FakeStructuredModel(
            [interpreter_result(supporting_fact_ids=("invented-fact",))]
        )
        with self.assertRaises(StructuredModelError):
            await TechnologyInterpreter(model, KnowledgeSemanticRegistry()).interpret(selection, [])

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
                    supporting_fact_ids=(fact.fact_id,),
                )
            ]
        )
        observations, _ = await TechnologyInterpreter(model, KnowledgeSemanticRegistry()).interpret(selection, [fact])
        tapeout = next(item for item in observations if item.milestone_id == "tapeout")
        self.assertEqual(tapeout.status, "supported")
        self.assertTrue(tapeout.blocked_inferences)
        self.assertIn("不能证明已量产", tapeout.reason)

        no_fact_model = FakeStructuredModel(
            [interpreter_result("semiconductor_design", "tapeout", supporting_fact_ids=())]
        )
        no_fact_observations, _ = await TechnologyInterpreter(
            no_fact_model, KnowledgeSemanticRegistry()
        ).interpret(selection, [fact])
        no_fact_tapeout = next(item for item in no_fact_observations if item.milestone_id == "tapeout")
        self.assertEqual(no_fact_tapeout.status, "limited_support")


if __name__ == "__main__":
    unittest.main()
