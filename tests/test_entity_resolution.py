"""Offline tests for identity resolution, company relevance and source typing."""

from __future__ import annotations

import hashlib
import gc
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.knowledge.contracts import Citation, KnowledgeChunk, Source, SourceType, SourceVersion
from app.knowledge.identity import company_for_name
from app.knowledge.semantic.extractor import source_quality
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.research.contracts import SearchRequest, SearchResult
from app.research.entity_resolution import (
    EnterpriseRelevanceGate,
    EntityResolutionDraft,
    EntityResolutionResult,
    IdentityResolver,
    discover_self_attested_official_website,
    SourceRelevanceDraft,
    _validate_urls,
    _is_official_host,
)
from app.research.orchestrator import IterativeResearchService
from app.research.planner import RetrievalPlanner
from app.research.search_provider import WebSearchProvider
from app.research.source_classifier import SourceTypeClassifier


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, prompt, payload):
        self.calls.append({"prompt": prompt, "payload": payload})
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


class FakeSearch(WebSearchProvider):
    name = "fake"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def search(self, request: SearchRequest):
        self.calls.append(request)
        return self.pages.get(request.query, [])

    async def extract(self, urls):
        return {}


def plan(identity_query="星辰科技 官网", deep_query="星辰科技 技术", include_deep=True):
    return {
        "company_identity_queries": [{"query": identity_query, "category": "company_identity"}],
        "business_queries": [],
        "technology_queries": [{"query": deep_query, "category": "technology"}] if include_deep else [],
        "product_queries": [], "people_queries": [],
        "reason": "先核实企业主体，再检索技术信息。", "target_categories": ["company_identity", "technology"], "information_gaps": [],
    }


def result(url, title, body):
    return SearchResult(title=title, url=url, content=body[:180], raw_content="# 公司信息\n\n" + body, provider="fake")


class EntityResolutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.kb = SharedKnowledgeBase(Path(self.temp.name), embedding_provider=LocalHashingEmbeddingProvider(dimensions=128))

    async def asyncTearDown(self):
        self.kb.close()
        gc.collect()
        self.temp.cleanup()

    async def test_same_name_companies_are_ambiguous_and_never_ingested(self):
        identity_q, deep_q = "星辰科技 官网", "星辰科技 技术"
        beijing = result("https://beijing.example.cn/company", "北京星辰科技有限公司", "北京星辰科技有限公司成立于北京，主营工业视觉设备。")
        shenzhen = result("https://shenzhen.example.cn/company", "深圳星辰科技有限公司", "深圳星辰科技有限公司位于深圳，主营通信模组。")
        response = {"status": "ambiguous", "canonical_name": None, "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [beijing.url, shenzhen.url], "identity_candidates": [
            {"canonical_name": "北京星辰科技有限公司", "official_website": None, "unified_social_credit_code": None, "evidence_urls": [beijing.url], "reason": "北京主体资料"},
            {"canonical_name": "深圳星辰科技有限公司", "official_website": None, "unified_social_credit_code": None, "evidence_urls": [shenzhen.url], "reason": "深圳主体资料"},
        ], "reason": "检索到两个同名主体，现有结果不足以区分。"}
        model = FakeModel([plan(identity_q, deep_q), response])
        search = FakeSearch({identity_q: [beijing, shenzhen], deep_q: [result("https://deep.example.cn", "技术页面", "不应被执行") ]})
        service = IterativeResearchService(RetrievalPlanner(model), search, self.kb)
        outcome = await service.run("星辰科技", max_rounds=3)
        self.assertEqual(outcome.entity_resolution_status, "ambiguous")
        self.assertEqual(outcome.stop_reason, "entity_ambiguous")
        self.assertEqual(len(outcome.identity_candidates), 2)
        self.assertEqual([x.query for x in search.calls], [identity_q])
        self.assertEqual(self.kb.repository.counts()["sources"], 0)
        self.assertEqual(self.kb.repository.counts()["knowledge_chunks"], 0)

    async def test_resolved_website_persists_and_classifies_first_party(self):
        query = "某科技公司 官网"
        body = "某科技公司（某科技公司股份有限公司）运营的官方网站（https://company.example.com/，以下简称为‘本网站’）。" + "公开资料。" * 100
        homepage = result("https://company.example.com/about", "某科技公司股份有限公司官方网站", body)
        resolved = {"status": "resolved", "canonical_name": "某科技公司股份有限公司", "aliases": ["某科技公司"], "official_website": "https://company.example.com", "unified_social_credit_code": None, "evidence_urls": [homepage.url], "identity_candidates": [], "reason": "官方页面明确标示企业主体与官网。"}
        model = FakeModel([plan(query, include_deep=False), resolved])
        service = IterativeResearchService(RetrievalPlanner(model), FakeSearch({query: [homepage]}), self.kb)
        outcome = await service.run("某科技公司", max_rounds=1)
        self.assertEqual(outcome.entity_resolution_status, "resolved")
        self.assertEqual(outcome.resolved_canonical_name, "某科技公司股份有限公司")
        self.assertEqual(outcome.official_website, "https://company.example.com/")
        company = self.kb.repository.get_company(company_for_name("某科技公司").company_id)
        self.assertEqual(company.official_website, "https://company.example.com/")
        self.assertEqual(company.aliases, ["某科技公司"])
        self.assertEqual(company.metadata["resolution_evidence_urls"], [homepage.url])
        self.assertEqual(company.metadata["official_website_resolution_source"], "self_attested_page")
        self.assertEqual(company.metadata["official_website_evidence_url"], homepage.url)
        sources = self.kb.repository.list_current_chunks(company.company_id, "general")
        stored_source = self.kb.repository.get_source(sources[0].source_id)
        self.assertEqual(stored_source.source_type, SourceType.COMPANY_OFFICIAL)
        version = self.kb.repository.get_source_version(sources[0].source_version_id)
        quality = source_quality(stored_source, sources[0], version)
        self.assertEqual(quality.category, "first_party")
        self.assertEqual(len(model.calls), 2)  # Initial planner and entity resolver; official host fast path skips relevance LLM.

    async def test_model_draft_omits_input_name_and_application_restores_it(self):
        page = result(
            "https://www.cambricon.com/legal",
            "寒武纪法律声明",
            "本法律声明适用于中科寒武纪科技股份有限公司……本公司运营的官方网站：https://www.cambricon.com/（以下简称本网站）。",
        )
        response = {
            "status": "resolved",
            "canonical_name": "中科寒武纪科技股份有限公司",
            "aliases": ["寒武纪"],
            "official_website": "https://www.cambricon.com/",
            "unified_social_credit_code": None,
            "evidence_urls": [page.url],
            "identity_candidates": [],
            "reason": "企业法律声明支持该主体",
        }
        model = FakeModel([response])
        output = await IdentityResolver(model).resolve("中科寒武纪科技股份有限公司", [page])
        self.assertEqual(output.input_name, "中科寒武纪科技股份有限公司")
        self.assertEqual(output.official_website, "https://www.cambricon.com/")
        self.assertEqual(output.official_website_resolution_source, "self_attested_page")
        self.assertNotIn("input_name", response)
        self.assertNotIn("input_name", EntityResolutionDraft.model_fields)
        self.assertIn("不要返回或重述 input_name", model.calls[0]["prompt"])

    async def test_identity_draft_requires_evidence_url_in_schema_and_repairs_empty_list(self):
        schema = EntityResolutionDraft.model_json_schema()
        self.assertIn("evidence_urls", schema["required"])
        self.assertEqual(schema["properties"]["evidence_urls"]["minItems"], 1)

        page = result("https://example.test/result", "示例科技股份有限公司", "示例科技股份有限公司公开资料。")
        invalid = {
            "status": "resolved", "canonical_name": "示例科技股份有限公司", "aliases": [],
            "official_website": None, "unified_social_credit_code": None,
            "evidence_urls": [], "identity_candidates": [], "reason": "证据支持主体",
        }
        repaired = {**invalid, "evidence_urls": [page.url]}
        model = FakeModel([invalid, repaired])
        output = await IdentityResolver(model).resolve("示例科技股份有限公司", [page])
        self.assertEqual(output.input_name, "示例科技股份有限公司")
        self.assertEqual(output.evidence_urls, [page.url])
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[1]["payload"]["validation_errors"][0]["type"], "too_short")

    async def test_unresolved_with_results_must_reference_checked_url(self):
        page = result("https://example.test/result", "示例科技", "现有资料不足以唯一确认主体。")
        response = {
            "status": "unresolved", "canonical_name": None, "aliases": [],
            "official_website": None, "unified_social_credit_code": None,
            "evidence_urls": [page.url], "identity_candidates": [],
            "reason": "现有资料不足以唯一确认主体",
        }
        output = await IdentityResolver(FakeModel([response])).resolve("示例科技", [page])
        self.assertEqual(output.status, "unresolved")
        self.assertEqual(output.evidence_urls, [page.url])

    async def test_empty_search_results_keep_deterministic_unresolved_without_model_call(self):
        model = FakeModel([])
        output = await IdentityResolver(model).resolve("示例科技", [])
        self.assertEqual(output.status, "unresolved")
        self.assertEqual(output.input_name, "示例科技")
        self.assertEqual(output.evidence_urls, [])
        self.assertEqual(model.calls, [])

    async def test_official_host_www_apex_subdomain_and_evil_domain(self):
        official = "https://www.example.com/"
        self.assertTrue(_is_official_host("https://www.example.com/about", official))
        self.assertTrue(_is_official_host("https://example.com/news", official))
        self.assertTrue(_is_official_host("https://developer.example.com/", official))
        self.assertFalse(_is_official_host("https://evil-example.com/", official))

    async def test_deterministic_identity_failures_have_safe_field_diagnostics(self):
        page = result("https://example.test/legal", "可信企业法律声明", "可信企业官方资料。")

        async def assert_error(response, category, location, code):
            with self.subTest(code=code):
                with self.assertRaises(Exception) as raised:
                    await IdentityResolver(FakeModel([response])).resolve("输入企业", [page])
                error = raised.exception
                self.assertEqual(error.category, category)
                self.assertEqual(error.diagnostics[0]["location"], location)
                self.assertEqual(error.diagnostics[0]["type"], code)
                self.assertNotIn("输入企业", str(error.diagnostics))
                self.assertNotIn("完整模型输出", str(error.diagnostics))

        base = {
            "canonical_name": "可信企业", "aliases": [], "official_website": None,
            "unified_social_credit_code": None, "evidence_urls": [page.url],
            "identity_candidates": [], "reason": "evidence check",
        }
        await assert_error(
            {**base, "status": "resolved", "unified_social_credit_code": "123"},
            "invalid_identity_claim", "unified_social_credit_code", "malformed_uscc",
        )
        await assert_error(
            {**base, "status": "resolved", "canonical_name": "不存在的企业"},
            "unsupported_identity_claim", "canonical_name", "unsupported_canonical_name",
        )
        await assert_error(
            {**base, "status": "resolved", "aliases": ["未获支持的别名"]},
            "unsupported_identity_claim", "alias", "unsupported_alias",
        )
        await assert_error(
            {**base, "status": "resolved", "unified_social_credit_code": "AB1234567890123456"},
            "unsupported_identity_claim", "unified_social_credit_code", "unsupported_uscc",
        )

    async def test_missing_evidence_ambiguous_count_and_foreign_evidence_diagnostics(self):
        page = result("https://example.test/legal", "可信企业法律声明", "可信企业官方资料。")
        cases = [
            ({"status": "resolved", "canonical_name": "可信企业", "aliases": [],
              "official_website": None, "unified_social_credit_code": None,
              "evidence_urls": ["https://foreign.test/"], "identity_candidates": [],
              "reason": "foreign evidence"},
             "identity_evidence_url_not_in_results", "evidence_urls"),
        ]
        for response, code, location in cases:
            with self.subTest(code=code):
                with self.assertRaises(Exception) as raised:
                    await IdentityResolver(FakeModel([response])).resolve("输入企业", [page])
                self.assertEqual(raised.exception.category, "invalid_identity_claim")
                self.assertEqual(raised.exception.diagnostics[0]["type"], code)
                self.assertEqual(raised.exception.diagnostics[0]["location"], location)

        with self.assertRaises(Exception) as raised:
            _validate_urls([], {page.url: page}, required=True)
        self.assertEqual(raised.exception.diagnostics[0]["type"], "resolved_missing_evidence")

    async def test_unverified_model_website_is_not_accepted_without_page_attestation(self):
        page = result("https://example-news.com/story", "星辰科技报道", "星辰科技发布一款新产品。")
        response = {"status": "resolved", "canonical_name": "星辰科技", "aliases": [], "official_website": "https://invented-company.com", "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "模型猜测的官网。"}
        model = FakeModel([response])
        output = await IdentityResolver(model).resolve("星辰科技", [page])
        self.assertEqual(set(model.calls[0]["payload"]), {"input_name", "identity_search_results"})
        self.assertIsNone(output.official_website)
        self.assertEqual(output.official_website_diagnostic, "official_website_not_self_attested")

    async def test_official_website_self_attestation_requires_company_marker_and_own_host(self):
        page = result(
            "https://www.example-company.cn/about", "示例科技股份有限公司",
            "示例科技股份有限公司运营的官方网站，本网站：https://www.example-company.cn/。",
        )
        response = {"status": "resolved", "canonical_name": "示例科技股份有限公司", "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "页面支持主体。"}
        output = await IdentityResolver(FakeModel([response])).resolve("示例科技", [page])
        self.assertEqual(output.official_website, "https://www.example-company.cn/")
        self.assertEqual(output.official_website_resolution_source, "self_attested_page")
        self.assertEqual(output.official_website_evidence_url, page.url)

    async def test_self_attestation_uses_one_local_window_and_rejects_directory_footer(self):
        canonical = "示例芯片股份有限公司"
        page = result(
            "https://directory.example.com/company/example-chip",
            "示例芯片股份有限公司官网 - 企业导航",
            "示例芯片股份有限公司官网： https://www.example-chip.com/。"
            + ("无关目录正文。" * 80)
            + "本站首页：https://directory.example.com/",
        )
        response = {"status": "resolved", "canonical_name": canonical, "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "主体证据足够。"}
        output = await IdentityResolver(FakeModel([response])).resolve(canonical, [page])
        self.assertIsNone(output.official_website)
        self.assertIsNone(output.official_website_resolution_source)

    async def test_self_attestation_rejects_own_host_url_far_from_company_claim(self):
        canonical = "示例芯片股份有限公司"
        page = result(
            "https://directory.example.com/company/example-chip",
            "目录资料",
            f"{canonical}官方网站 https://company.example.com/。"
            + ("无关内容。" * 160)
            + "本站首页：https://directory.example.com/",
        )
        response = {"status": "resolved", "canonical_name": canonical, "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "主体证据足够。"}
        output = await IdentityResolver(FakeModel([response])).resolve(canonical, [page])
        self.assertIsNone(output.official_website)

    async def test_self_attestation_does_not_promote_cross_host_official_link(self):
        canonical = "示例芯片股份有限公司"
        page = result(
            "https://news.example.com/story",
            "示例芯片股份有限公司官网信息",
            f"{canonical}官方网站 https://company.example.com/。",
        )
        response = {"status": "resolved", "canonical_name": canonical, "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "主体证据足够。"}
        output = await IdentityResolver(FakeModel([response])).resolve(canonical, [page])
        self.assertIsNone(output.official_website)

    async def test_self_attestation_accepts_validated_alias_in_local_window(self):
        canonical = "中科寒武纪科技股份有限公司"
        alias = "寒武纪"
        page = result(
            "https://www.example-chip.com/legal",
            canonical,
            f"{canonical}（{alias}）运营的官方网站，本网站：https://www.example-chip.com/。",
        )
        response = {"status": "resolved", "canonical_name": canonical, "aliases": [alias], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "主体与别名均有证据。"}
        output = await IdentityResolver(FakeModel([response])).resolve(canonical, [page])
        self.assertEqual(output.official_website, "https://www.example-chip.com/")
        self.assertEqual(output.official_website_resolution_source, "self_attested_page")

    def test_duplicate_self_attested_host_keeps_first_evidence_page(self):
        canonical = "示例芯片股份有限公司"
        first = result("https://www.example-chip.com/legal", canonical, f"{canonical}运营的官方网站，本网站 https://www.example-chip.com/")
        second = result("https://example-chip.com/about", canonical, f"{canonical}运营的官方网站，本网站 https://example-chip.com/")
        discovery = discover_self_attested_official_website([canonical, canonical], [first, second])
        self.assertEqual(discovery.website, "https://www.example-chip.com/")
        self.assertEqual(discovery.evidence_url, first.url)
        self.assertEqual(discovery.candidate_hosts, ("example-chip.com",))

    async def test_self_attestation_rejects_third_party_host_and_reports_conflict(self):
        first = result("https://one.example.cn/about", "示例科技股份有限公司", "示例科技股份有限公司运营的官方网站，本网站 https://one.example.cn/")
        second = result("https://two.example.cn/about", "示例科技股份有限公司", "示例科技股份有限公司运营的官方网站，本网站 https://two.example.cn/")
        third_party = result("https://news.example.cn/story", "示例科技股份有限公司", "示例科技股份有限公司官网 https://company.example.cn/")
        response = {"status": "resolved", "canonical_name": "示例科技股份有限公司", "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [first.url, second.url, third_party.url], "identity_candidates": [], "reason": "主体证据足够。"}
        third_party_response = {**response, "evidence_urls": [third_party.url]}
        third_party_output = await IdentityResolver(FakeModel([third_party_response])).resolve("示例科技", [third_party])
        self.assertIsNone(third_party_output.official_website)
        output = await IdentityResolver(FakeModel([response])).resolve("示例科技", [first, second, third_party])
        self.assertIsNone(output.official_website)
        self.assertEqual(output.official_website_diagnostic, "official_website_candidate_conflict")

    async def test_university_alumni_website_claim_is_only_an_external_hint(self):
        canonical = "示例芯片股份有限公司"
        alumni = result(
            "https://alumni.university.edu/company/123",
            "校友企业介绍",
            f"{canonical}是一家芯片企业。公司网址：www.example-chip.com。页面资源包括 tagline-zh-hans.svg 和 index.php。",
        )
        response = {
            "status": "resolved", "canonical_name": canonical, "aliases": [],
            "official_website": "https://www.example-chip.com/",
            "unified_social_credit_code": None, "evidence_urls": [alumni.url],
            "identity_candidates": [], "reason": "第三方页面提到公司网址。",
        }
        output = await IdentityResolver(FakeModel([response])).resolve(canonical, [alumni])
        self.assertIsNone(output.official_website)
        self.assertIsNone(output.official_website_resolution_source)
        self.assertEqual(output.official_website_external_candidate_hosts, ["example-chip.com"])
        self.assertEqual(output.official_website_strong_candidate_hosts, [])

    async def test_official_privacy_page_can_self_attest_but_generic_homepage_cannot(self):
        canonical = "示例科技股份有限公司"
        privacy = result(
            "https://www.example.com/privacy",
            "隐私政策",
            f"本隐私政策适用于{canonical}通过官方网站向用户提供的服务。本网站：https://www.example.com/。",
        )
        generic = result(
            "https://www.generic-example.com/about",
            canonical,
            f"{canonical}产品介绍。",
        )
        privacy_payload = {
            "status": "resolved", "canonical_name": canonical, "aliases": [],
            "official_website": None, "unified_social_credit_code": None,
            "evidence_urls": [privacy.url], "identity_candidates": [], "reason": "隐私声明。",
        }
        generic_payload = {
            **privacy_payload,
            "official_website": "https://www.generic-example.com/",
            "evidence_urls": [generic.url],
        }
        accepted = await IdentityResolver(FakeModel([privacy_payload])).resolve(canonical, [privacy])
        rejected = await IdentityResolver(FakeModel([generic_payload])).resolve(canonical, [generic])
        self.assertEqual(accepted.official_website, "https://www.example.com/")
        self.assertEqual(accepted.official_website_resolution_source, "self_attested_page")
        self.assertIsNone(rejected.official_website)
        self.assertEqual(rejected.official_website_diagnostic, "official_website_not_self_attested")

    def test_entity_resolution_draft_enforces_status_invariants(self):
        base = {"status": "resolved", "canonical_name": "企业", "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": ["https://example.test"], "identity_candidates": [], "reason": "依据"}
        with self.assertRaises(Exception):
            EntityResolutionDraft.model_validate({**base, "canonical_name": None})
        with self.assertRaises(Exception):
            EntityResolutionDraft.model_validate({**base, "status": "ambiguous", "canonical_name": None})
        with self.assertRaises(Exception):
            EntityResolutionDraft.model_validate({**base, "status": "unresolved", "canonical_name": None, "aliases": ["别名"]})

    async def test_irrelevant_same_name_page_is_rejected(self):
        resolved_company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "resolution_status": "resolved"})
        page = result("https://news.example.cn/shenzhen", "深圳星辰科技新品发布", "深圳星辰科技有限公司发布通信模组新品。")
        gate = EnterpriseRelevanceGate(FakeModel([{"decisions": [{"page_id": "P1", "status": "irrelevant", "evidence_quote": "深圳星辰科技有限公司发布通信模组新品。", "reason": "主体名称对应深圳公司而非已解析的北京公司。"}]}]))
        decisions = await gate.assess(resolved_company, [page])
        self.assertEqual(decisions[0].status, "irrelevant")
        self.assertEqual(decisions[0].decision_source, "model")

    async def test_uncertain_decision_is_available_and_not_fast_path(self):
        company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "official_website": "https://beijing.example.cn", "resolution_status": "resolved"})
        page = result("https://news.example.cn/maybe", "星辰科技", "仅有短摘要，不能区分企业主体。")
        gate = EnterpriseRelevanceGate(FakeModel([{"decisions": [{"page_id": "P1", "status": "uncertain", "evidence_quote": "", "reason": "页面不足以区分同名主体。"}]}]))
        decision = (await gate.assess(company, [page]))[0]
        self.assertEqual(decision.status, "uncertain")

    async def test_irrelevant_and_uncertain_pages_are_not_ingested_or_counted_as_sources(self):
        for index, gate_status in enumerate(("irrelevant", "uncertain")):
            with self.subTest(status=gate_status):
                query = f"星河{index}科技 官网"
                canonical = f"星河{index}科技有限公司"
                page = result(
                    f"https://identity{index}.example.cn/company",
                    canonical,
                    f"{canonical}成立于北京，提供工业传感器产品。" + "公司公开介绍。" * 100,
                )
                resolved = {
                    "status": "resolved", "canonical_name": canonical,
                    "aliases": [], "official_website": None, "unified_social_credit_code": None,
                    "evidence_urls": [page.url], "identity_candidates": [], "reason": "搜索结果包含主体名称。",
                }
                evidence = f"{canonical}成立于北京，提供工业传感器产品。"
                relevance = {"decisions": [{
                    "page_id": "P1", "status": gate_status,
                    "evidence_quote": evidence if gate_status == "irrelevant" else "",
                    "reason": "该网页不能确认与目标公司有关。",
                }]}
                company = company_for_name(canonical).model_copy(update={"canonical_name": canonical, "resolution_status": "resolved"})
                gate = EnterpriseRelevanceGate(FakeModel([relevance]))
                service = IterativeResearchService(RetrievalPlanner(FakeModel([])), FakeSearch({}), self.kb, relevance_gate=gate)
                relevant, _, counts, _, _ = await service._gate_and_ingest(
                    [page], company,
                    {"ingested": 0, "new_versions": 0, "duplicate_versions": 0, "chunks": 0, "relevant": 0, "irrelevant": 0, "uncertain": 0},
                    set(),
                )
                self.assertEqual(relevant, [])
                self.assertEqual(counts["ingested"], 0)
                self.assertEqual(counts[gate_status], 1)
                self.assertEqual(self.kb.repository.counts()["sources"], 0)

    async def test_relevance_gate_downgrades_paraphrase_and_counts_diagnostic(self):
        company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "resolution_status": "resolved"})
        page = result("https://news.example.cn/shenzhen", "深圳星辰科技新品发布", "深圳星辰科技有限公司发布通信模组新品。")
        model = FakeModel([{"decisions": [{"page_id": "P1", "status": "relevant", "evidence_quote": "深圳公司推出新的通信产品", "reason": "模型作了改写。"}]}])
        gate = EnterpriseRelevanceGate(model)
        decision = (await gate.assess(company, [page]))[0]
        self.assertEqual(decision.status, "uncertain")
        self.assertEqual(decision.downgrade_reason, "evidence_quote_not_verified")
        self.assertEqual(gate.last_diagnostics["relevance_downgrade_count"], 1)
        self.assertNotIn("url", SourceRelevanceDraft.model_fields)
        self.assertEqual(model.calls[0]["payload"]["pages"][0]["page_id"], "P1")

    async def test_relevance_gate_missing_duplicate_and_unknown_page_references_fail_closed(self):
        company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "resolution_status": "resolved"})
        pages = [result(f"https://news.example.cn/{i}", f"新闻 {i}", f"企业资料 {i}。") for i in range(1, 4)]
        output = {"decisions": [
            {"page_id": "P1", "status": "relevant", "evidence_quote": "企业资料 1。", "reason": "有精确证据。"},
            {"page_id": "P2", "status": "irrelevant", "evidence_quote": "企业资料 2。", "reason": "页面与企业无关。"},
            {"page_id": "P2", "status": "relevant", "evidence_quote": "企业资料 2。", "reason": "重复冲突。"},
            {"page_id": "P99", "status": "relevant", "evidence_quote": "未知页面", "reason": "越界引用。"},
        ]}
        gate = EnterpriseRelevanceGate(FakeModel([output]))
        decisions = await gate.assess(company, pages)
        by_url = {item.url: item for item in decisions}
        self.assertEqual(by_url[pages[0].url].status, "relevant")
        self.assertEqual(by_url[pages[1].url].status, "uncertain")
        self.assertEqual(by_url[pages[1].url].downgrade_reason, "duplicate_model_decision")
        self.assertEqual(by_url[pages[2].url].status, "uncertain")
        self.assertEqual(by_url[pages[2].url].downgrade_reason, "missing_model_decision")
        self.assertEqual(gate.last_diagnostics["relevance_duplicate_decision_count"], 1)
        self.assertEqual(gate.last_diagnostics["relevance_missing_decision_count"], 1)
        self.assertEqual(gate.last_diagnostics["relevance_unknown_reference_count"], 1)
        self.assertIn("unknown_page_reference", gate.last_diagnostics["warnings"])

    async def test_relevance_gate_model_failure_downgrades_pending_but_keeps_trusted_fast_paths(self):
        company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "official_website": "https://beijing.example.cn", "resolution_status": "resolved"})
        identity = result("https://registry.example.cn/company", "企业登记", "北京星辰科技有限公司登记信息。")
        official = result("https://www.beijing.example.cn/news", "官网新闻", "北京星辰科技有限公司官网新闻。")
        pending = result("https://news.example.cn/item", "新闻", "新闻内容。")
        class FailingModel:
            async def complete_json(self, prompt, payload):
                raise TimeoutError("provider timeout")
        gate = EnterpriseRelevanceGate(FailingModel())
        decisions = await gate.assess(company, [identity, official, pending], trusted_identity_urls=[identity.url])
        by_url = {item.url: item for item in decisions}
        self.assertEqual(by_url[identity.url].decision_source, "identity_evidence")
        self.assertEqual(by_url[official.url].decision_source, "official_host")
        self.assertEqual(by_url[pending.url].status, "uncertain")
        self.assertEqual(by_url[pending.url].decision_source, "fallback")
        self.assertTrue(gate.last_diagnostics["relevance_gate_fallback"])
        self.assertEqual(gate.last_diagnostics["relevance_gate_fallback_count"], 1)
        self.assertEqual(gate.last_diagnostics["identity_evidence_fast_path_count"], 1)
        self.assertEqual(gate.last_diagnostics["official_host_fast_path_count"], 1)

    def test_source_type_classifier_supported_hosts(self):
        classifier = SourceTypeClassifier()
        official = company_for_name("示例公司").model_copy(update={"official_website": "https://company.example.com"})
        cases = [
            ("https://company.example.com/report", "general", official, SourceType.COMPANY_OFFICIAL),
            ("https://www.most.gov.cn/policy", "general", None, SourceType.GOVERNMENT),
            ("https://www.nfra.gov.cn/rules", "general", None, SourceType.REGULATORY),
            ("https://www.nfra.gov.cn/news", "news", None, SourceType.REGULATORY),
            ("https://patents.google.com/patent/US123", "general", None, SourceType.PATENT),
            ("https://pubmed.ncbi.nlm.nih.gov/12345", "general", None, SourceType.PAPER),
            ("https://news.example.com/item", "news", None, SourceType.NEWS),
            ("https://unknown.example.org/item", "general", None, SourceType.WEB),
        ]
        for url, topic, company, expected in cases:
            with self.subTest(url=url):
                item = SearchResult(title="source", url=url, content="summary", provider="test", topic=topic)
                self.assertEqual(classifier.classify(item, company), expected)

        hint_only = official.model_copy(update={
            "official_website": None,
            "metadata": {"unverified_official_website_candidates": ["company.example.com"]},
        })
        alumni_page = SearchResult(
            title="校友企业介绍",
            url="https://alumni.university.edu/company/123",
            content="示例公司网址：www.company.example.com",
            provider="test",
        )
        self.assertEqual(classifier.classify(alumni_page, hint_only), SourceType.WEB)

    def test_exchange_disclosure_host_classification_excludes_finance_portals(self):
        classifier = SourceTypeClassifier()
        cases = [
            ("https://www.cninfo.com.cn/new/disclosure/detail", SourceType.EXCHANGE_DISCLOSURE),
            ("https://static.sse.com.cn/disclosure/listedinfo/announcement", SourceType.EXCHANGE_DISCLOSURE),
            ("https://www.szse.cn/disclosure/listed/fixed/index.html", SourceType.EXCHANGE_DISCLOSURE),
            ("https://notice.10jqka.com.cn/report", SourceType.WEB),
            ("https://xueqiu.com/S/SH688256", SourceType.WEB),
            ("https://finance.sina.com.cn/stock/company", SourceType.WEB),
            ("https://www.sohu.com/a/123", SourceType.WEB),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                item = SearchResult(title="披露信息", url=url, content="公司公告", provider="test")
                self.assertEqual(classifier.classify(item), expected)

    def test_exchange_disclosure_maps_to_authoritative_public_record(self):
        source = Source(source_id="src", source_type=SourceType.EXCHANGE_DISCLOSURE, title="上市公司公告")
        version = SourceVersion(
            source_version_id="ver", source_id="src", content_sha256="a" * 64,
            retrieved_at=datetime.now(timezone.utc),
        )
        citation = Citation(citation_id="cit", source_id="src", source_version_id="ver", source_title="上市公司公告", excerpt="公告正文")
        chunk = KnowledgeChunk(chunk_id="chunk", text="公告正文", source_id="src", source_version_id="ver", citation=citation)
        quality = source_quality(source, chunk, version)
        self.assertEqual(quality.category, "authoritative_public_record")
        self.assertIn("法定披露平台", quality.rationale)

    def test_unknown_and_federal_registry_host_types(self):
        classifier = SourceTypeClassifier()
        gsxt = SearchResult(title="登记公示", url="https://www.gsxt.gov.cn/index.html", content="主体信息", provider="test")
        cnipa = SearchResult(title="专利", url="https://www.cnipa.gov.cn/patent", content="专利信息", provider="test")
        self.assertEqual(classifier.classify(gsxt), SourceType.REGISTRY)
        self.assertEqual(classifier.classify(cnipa), SourceType.PATENT)


if __name__ == "__main__":
    unittest.main()
