"""Offline tests for identity resolution, company relevance and source typing."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.knowledge.contracts import SourceType
from app.knowledge.identity import company_for_name
from app.knowledge.semantic.extractor import source_quality
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.research.contracts import SearchRequest, SearchResult
from app.research.entity_resolution import EnterpriseRelevanceGate, EntityResolutionResult, IdentityResolver
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
        self.temp.cleanup()

    async def test_same_name_companies_are_ambiguous_and_never_ingested(self):
        identity_q, deep_q = "星辰科技 官网", "星辰科技 技术"
        beijing = result("https://beijing.example.cn/company", "北京星辰科技有限公司", "北京星辰科技有限公司成立于北京，主营工业视觉设备。")
        shenzhen = result("https://shenzhen.example.cn/company", "深圳星辰科技有限公司", "深圳星辰科技有限公司位于深圳，主营通信模组。")
        response = {"status": "ambiguous", "input_name": "星辰科技", "canonical_name": None, "aliases": [], "official_website": None, "unified_social_credit_code": None, "evidence_urls": [], "identity_candidates": [
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
        body = "某科技公司（某科技公司股份有限公司）官网介绍公司主营技术产品及研发团队。" + "公开资料。" * 100
        homepage = result("https://company.example.com/about", "某科技公司股份有限公司官方网站", body)
        resolved = {"status": "resolved", "input_name": "某科技公司", "canonical_name": "某科技公司股份有限公司", "aliases": ["某科技公司"], "official_website": "https://company.example.com", "unified_social_credit_code": None, "evidence_urls": [homepage.url], "identity_candidates": [], "reason": "官方页面明确标示企业主体与官网。"}
        model = FakeModel([plan(query, include_deep=False), resolved])
        service = IterativeResearchService(RetrievalPlanner(model), FakeSearch({query: [homepage]}), self.kb)
        outcome = await service.run("某科技公司", max_rounds=1)
        self.assertEqual(outcome.entity_resolution_status, "resolved")
        self.assertEqual(outcome.resolved_canonical_name, "某科技公司股份有限公司")
        self.assertEqual(outcome.official_website, "https://company.example.com")
        company = self.kb.repository.get_company(company_for_name("某科技公司").company_id)
        self.assertEqual(company.official_website, "https://company.example.com")
        self.assertEqual(company.aliases, ["某科技公司"])
        self.assertEqual(company.metadata["resolution_evidence_urls"], [homepage.url])
        sources = self.kb.repository.list_current_chunks(company.company_id, "general")
        stored_source = self.kb.repository.get_source(sources[0].source_id)
        self.assertEqual(stored_source.source_type, SourceType.COMPANY_OFFICIAL)
        version = self.kb.repository.get_source_version(sources[0].source_version_id)
        quality = source_quality(stored_source, sources[0], version)
        self.assertEqual(quality.category, "first_party")
        self.assertEqual(len(model.calls), 2)  # Initial planner and entity resolver; official host fast path skips relevance LLM.

    async def test_model_cannot_invent_official_website(self):
        page = result("https://example-news.com/story", "星辰科技报道", "星辰科技发布一款新产品。")
        response = {"status": "resolved", "input_name": "星辰科技", "canonical_name": "星辰科技", "aliases": [], "official_website": "https://invented-company.com", "unified_social_credit_code": None, "evidence_urls": [page.url], "identity_candidates": [], "reason": "模型猜测的官网。"}
        model = FakeModel([response])
        with self.assertRaises(Exception) as raised:
            await IdentityResolver(model).resolve("星辰科技", [page])
        self.assertEqual(set(model.calls[0]["payload"]), {"input_name", "identity_search_results"})
        self.assertEqual(getattr(raised.exception, "category", None), "unsupported_identity_claim")

    async def test_irrelevant_same_name_page_is_rejected(self):
        resolved_company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "resolution_status": "resolved"})
        page = result("https://news.example.cn/shenzhen", "深圳星辰科技新品发布", "深圳星辰科技有限公司发布通信模组新品。")
        gate = EnterpriseRelevanceGate(FakeModel([{"decisions": [{"status": "irrelevant", "url": page.url, "evidence": "深圳星辰科技有限公司发布通信模组新品", "reason": "主体名称对应深圳公司而非已解析的北京公司。"}]}]))
        decisions = await gate.assess(resolved_company, [page])
        self.assertEqual(decisions[0].status, "irrelevant")

    async def test_uncertain_decision_is_available_and_not_fast_path(self):
        company = company_for_name("北京星辰科技有限公司").model_copy(update={"canonical_name": "北京星辰科技有限公司", "official_website": "https://beijing.example.cn", "resolution_status": "resolved"})
        page = result("https://news.example.cn/maybe", "星辰科技", "仅有短摘要，不能区分企业主体。")
        gate = EnterpriseRelevanceGate(FakeModel([{"decisions": [{"status": "uncertain", "url": page.url, "evidence": "", "reason": "页面不足以区分同名主体。"}]}]))
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
                    "status": "resolved", "input_name": f"星河{index}科技", "canonical_name": canonical,
                    "aliases": [], "official_website": None, "unified_social_credit_code": None,
                    "evidence_urls": [page.url], "identity_candidates": [], "reason": "搜索结果包含主体名称。",
                }
                evidence = f"{canonical}成立于北京，提供工业传感器产品。"
                relevance = {"decisions": [{
                    "status": gate_status, "url": page.url,
                    "evidence": evidence if gate_status == "irrelevant" else "",
                    "reason": "该网页不能确认与目标公司有关。",
                }]}
                model = FakeModel([plan(query, include_deep=False), resolved, relevance])
                service = IterativeResearchService(RetrievalPlanner(model), FakeSearch({query: [page]}), self.kb)
                outcome = await service.run(f"星河{index}科技", max_rounds=1)

                self.assertEqual(outcome.entity_resolution_status, "resolved")
                self.assertEqual(outcome.sources_ingested, 0)
                self.assertEqual(outcome.knowledge_chunks_added, 0)
                self.assertEqual(outcome.relevant_source_count, 0)
                self.assertEqual(outcome.irrelevant_source_count, int(gate_status == "irrelevant"))
                self.assertEqual(outcome.uncertain_source_count, int(gate_status == "uncertain"))
                self.assertEqual(outcome.trace[0].source_type_counts, {})
                self.assertEqual(self.kb.repository.list_current_chunks(company_for_name(f"星河{index}科技").company_id, "general"), [])

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

    def test_unknown_and_federal_registry_host_types(self):
        classifier = SourceTypeClassifier()
        gsxt = SearchResult(title="登记公示", url="https://www.gsxt.gov.cn/index.html", content="主体信息", provider="test")
        cnipa = SearchResult(title="专利", url="https://www.cnipa.gov.cn/patent", content="专利信息", provider="test")
        self.assertEqual(classifier.classify(gsxt), SourceType.REGISTRY)
        self.assertEqual(classifier.classify(cnipa), SourceType.PATENT)


if __name__ == "__main__":
    unittest.main()
