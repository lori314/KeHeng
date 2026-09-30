"""Offline tests for iterative research, Tavily contracts and shared-KB ingestion."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.contracts import KnowledgeLayer, SourceType
from app.knowledge.identity import canonicalize_url, company_for_name, source_id_for
from app.knowledge.semantic.extractor import source_quality
from app.knowledge.shared_knowledge_base import KnowledgeSearchRequest, SharedKnowledgeBase
from app.rag.embedding import LocalHashingEmbeddingProvider
from app.research.contracts import SearchRequest, SearchResult
from app.research.ingestion import WebContentChunker
from app.research.orchestrator import IterativeResearchService
from app.research.planner import RetrievalPlanner
from app.research.entity_resolution import EnterpriseRelevanceGate, EntityResolutionResult, SourceRelevanceDecision
from app.research.source_classifier import SourceTypeClassifier
from app.research.search_provider import SearchProviderError, WebSearchProvider
from app.research.tavily_provider import TavilySearchProvider


class FakeStructuredModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, system_prompt, payload):
        self.calls.append({"prompt": system_prompt, "payload": payload})
        if not self.responses:
            raise AssertionError("unexpected planner call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSearchProvider(WebSearchProvider):
    name = "fake"

    def __init__(self, results_by_query=None, extracted=None):
        self.results_by_query = results_by_query or {}
        self.extracted = extracted or {}
        self.calls: list[SearchRequest] = []
        self.extract_calls: list[list[str]] = []

    async def search(self, request):
        self.calls.append(request)
        return list(self.results_by_query.get(request.query, []))

    async def extract(self, urls):
        self.extract_calls.append(list(urls))
        return {url: self.extracted[url] for url in urls if url in self.extracted}


class TestIdentityResolver:
    async def resolve(self, input_name, results):
        if not results:
            return EntityResolutionResult(status="unresolved", input_name=input_name, reason="test no identity evidence")
        return EntityResolutionResult(status="resolved", input_name=input_name, canonical_name=input_name, evidence_urls=[results[0].url], reason="test fixture identity resolver")


class TestRelevanceGate:
    async def assess(self, company, results, *, trusted_identity_urls=None):
        return [SourceRelevanceDecision(status="relevant", url=result.url, evidence=(result.content or result.raw_content or result.title)[:30], reason="test fixture relevance decision") for result in results]


def initial_plan(*queries):
    categories = {
        "company_identity_queries": [],
        "business_queries": [],
        "technology_queries": [],
        "product_queries": [],
        "people_queries": [],
    }
    for query, category in queries:
        field = {
            "company_identity": "company_identity_queries",
            "business": "business_queries",
            "technology": "technology_queries",
            "product": "product_queries",
            "people": "people_queries",
        }[category]
        categories[field].append({"query": query, "category": category})
    if not categories["company_identity_queries"]:
        first = next((item for field in ("business_queries", "technology_queries", "product_queries", "people_queries") for item in categories[field]), None)
        if first:
            categories["company_identity_queries"].append({**first, "category": "company_identity"})
    return {
        **categories,
        "reason": "从企业名称开始核实身份与公开技术信息。",
        "target_categories": [category for _, category in queries],
        "information_gaps": ["核心产品", "核心技术"],
    }


def next_plan(queries=(), *, terms=(), gaps=(), reason="依据本轮新来源继续核验。", should_continue=True):
    return {
        "new_queries": [
            {"query": query, "category": category}
            for query, category in queries
        ],
        "reason": reason,
        "target_categories": [category for _, category in queries],
        "discovered_terms": list(terms),
        "information_gaps": list(gaps),
        "should_continue": should_continue,
    }


def page(url, *, title="技术资料", body="公司公开资料提及 X1芯片、张三与 RISC-V。"):
    return SearchResult(
        title=title,
        url=url,
        content="搜索摘要：" + body[:100],
        raw_content=f"# 核心技术\n\n{body}\n\n第二段说明测试、验证和产品进展。",
        provider="fake",
    )


class IterativeResearchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.kb = SharedKnowledgeBase(
            Path(self.tempdir.name),
            embedding_provider=LocalHashingEmbeddingProvider(dimensions=256),
        )

    async def asyncTearDown(self):
        self.kb.close()
        for attempt in range(5):
            try:
                self.tempdir.cleanup()
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)

    def service(self, model_responses, results_by_query, **kwargs):
        model = FakeStructuredModel(model_responses)
        search = FakeSearchProvider(results_by_query)
        service = IterativeResearchService(
            RetrievalPlanner(model), search, self.kb,
            entity_resolver=kwargs.pop("entity_resolver", TestIdentityResolver()),
            relevance_gate=kwargs.pop("relevance_gate", TestRelevanceGate()),
            **kwargs,
        )
        return service, model, search

    async def test_name_only_input_creates_a_conservative_first_search_plan(self):
        service, model, search = self.service(
            [initial_plan(("某科技公司 官网", "company_identity"))],
            {"某科技公司 官网": [page("https://example.test/company")]},
        )
        result = await service.run("某科技公司", max_rounds=1)
        self.assertEqual(model.calls[0]["payload"]["enterprise_name"], "某科技公司")
        self.assertEqual(search.calls[0].query, "某科技公司 官网")
        self.assertEqual(result.rounds, 1)

    async def test_identity_search_enforces_result_limit_even_if_provider_overreturns(self):
        query = "某科技公司 官网"
        overreturned = [page(f"https://example.test/company/{index}") for index in range(7)]
        service, _, search = self.service(
            [initial_plan((query, "company_identity"))], {query: overreturned}
        )

        result = await service.run("某科技公司", max_rounds=1, max_results_per_query=5)

        self.assertEqual(search.calls[0].max_results, 5)
        self.assertEqual(result.sources_found, 5)
        self.assertEqual(result.trace[0].results_count, 5)

    async def test_followup_query_uses_a_term_found_in_first_round(self):
        first_query = "某科技公司 核心技术"
        followup = "某科技公司 X1芯片 客户验证"
        body = "产品为 X1芯片，核心人员张三负责 RISC-V 芯片架构研发。"
        service, _, search = self.service(
            [
                initial_plan((first_query, "technology")),
                next_plan(
                    [(followup, "technology")],
                    terms=["X1芯片", "张三", "RISC-V"],
                    gaps=["客户验证"],
                ),
            ],
            {first_query: [page("https://example.test/tech", body=body)], followup: []},
        )
        result = await service.run("某科技公司", max_rounds=2)
        self.assertEqual([call.query for call in search.calls], [first_query, followup])
        self.assertIn("X1芯片", search.calls[1].query)
        self.assertEqual(result.trace[0].new_terms, ["X1芯片", "张三", "RISC-V"])

    async def test_research_enrichment_updates_company_before_same_round_relevance_and_ingestion(self):
        identity_query, followup_query = "示例芯片股份有限公司 主体", "示例芯片股份有限公司 产品"
        identity_page = page("https://registry.example.net/company", body="示例芯片股份有限公司登记信息。" + "登记事项。" * 100)
        legal = page(
            "https://www.example-chip.com/legal",
            title="示例芯片股份有限公司官方网站",
            body="示例芯片股份有限公司运营的官方网站，本网站：https://www.example-chip.com/。" + "企业法律与产品信息。" * 120,
        )
        product = page(
            "https://www.example-chip.com/product",
            title="产品中心",
            body="示例芯片股份有限公司发布了 X9 芯片产品。" + "产品技术资料。" * 120,
        )
        planner_model = FakeStructuredModel([
            initial_plan((identity_query, "company_identity"), (followup_query, "product")),
        ])

        class ExplodingModel:
            async def complete_json(self, _prompt, _payload):
                raise AssertionError("official-host fast path should skip relevance model")

        class CapturingGate(EnterpriseRelevanceGate):
            async def assess(self, *args, **kwargs):
                decisions = await super().assess(*args, **kwargs)
                self.decisions = decisions
                return decisions

        gate = CapturingGate(ExplodingModel())
        search = FakeSearchProvider({identity_query: [identity_page], followup_query: [legal, product]})
        service = IterativeResearchService(
            RetrievalPlanner(planner_model), search, self.kb,
            entity_resolver=TestIdentityResolver(), relevance_gate=gate,
        )
        result = await service.run("示例芯片股份有限公司", max_rounds=2)

        self.assertEqual(result.entity_resolution_status, "resolved")
        self.assertEqual(result.rounds, 2, service.last_execution_trace)
        company = self.kb.repository.get_company(company_for_name("示例芯片股份有限公司").company_id)
        self.assertEqual(result.official_website_initial, None)
        self.assertEqual(result.official_website, "https://www.example-chip.com/")
        self.assertTrue(result.official_website_enriched_during_research)
        self.assertEqual(result.official_website_enrichment_round, 2)
        self.assertEqual(company.official_website, "https://www.example-chip.com/")
        self.assertEqual(company.metadata["official_website_resolution_source"], "self_attested_page")
        self.assertEqual(company.metadata["official_website_evidence_url"], legal.url)
        self.assertEqual(company.metadata["official_website_discovered_during"], "research")
        self.assertEqual(service.last_execution_trace["official_website_initial"], None)
        self.assertEqual(service.last_execution_trace["official_website_final"], "https://www.example-chip.com/")
        self.assertEqual(service.last_execution_trace["official_website_enrichment_round"], 2)
        self.assertEqual(gate.last_diagnostics["official_host_fast_path_count"], 2)
        self.assertEqual({x.decision_source for x in gate.decisions}, {"official_host"})
        self.assertTrue(all(x.status == "relevant" for x in gate.decisions))
        classifier = SourceTypeClassifier()
        for result_page in (legal, product):
            source_id = source_id_for(SourceType.COMPANY_OFFICIAL, canonical_url=canonicalize_url(result_page.url))
            stored = self.kb.repository.get_source(source_id)
            self.assertEqual(stored.source_type, SourceType.COMPANY_OFFICIAL)
            stored_chunks = self.kb.repository.list_current_chunks(company.company_id, KnowledgeLayer.GENERAL.value)
            source_chunk = next(x for x in stored_chunks if x.source_id == source_id)
            version = self.kb.repository.get_source_version(source_chunk.source_version_id)
            self.assertEqual(source_quality(stored, source_chunk, version).category, "first_party")
            self.assertEqual(classifier.classify(result_page, company), SourceType.COMPANY_OFFICIAL)

    async def test_research_website_conflict_does_not_update_company_or_stop_research(self):
        identity_query, followup_query = "示例芯片股份有限公司 主体", "示例芯片股份有限公司 产品"
        identity_page = page("https://registry.example.net/company", body="示例芯片股份有限公司登记信息。" + "登记事项。" * 100)
        first = page("https://one.example.com/legal", body="示例芯片股份有限公司运营的官方网站，本网站：https://one.example.com/。" + "正文资料。" * 100)
        second = page("https://two.example.com/legal", body="示例芯片股份有限公司运营的官方网站，本网站：https://two.example.com/。" + "正文资料。" * 100)
        model = FakeStructuredModel([
            initial_plan((identity_query, "company_identity"), (followup_query, "product")),
        ])
        search = FakeSearchProvider({identity_query: [identity_page], followup_query: [first, second]})
        service = IterativeResearchService(
            RetrievalPlanner(model), search, self.kb,
            entity_resolver=TestIdentityResolver(), relevance_gate=TestRelevanceGate(),
        )
        result = await service.run("示例芯片股份有限公司", max_rounds=2)
        self.assertEqual(result.entity_resolution_status, "resolved")
        self.assertEqual(result.rounds, 2, service.last_execution_trace)
        company = self.kb.repository.get_company(company_for_name("示例芯片股份有限公司").company_id)
        self.assertIsNone(company.official_website)
        self.assertEqual(result.official_website, None)
        self.assertEqual(result.official_website_enrichment_diagnostic, "official_website_candidate_conflict")
        self.assertGreaterEqual(result.sources_ingested, 2)
        self.assertEqual(service.last_execution_trace["official_website_enriched_during_research"], False)

    async def test_external_website_hints_are_deduplicated_across_research_rounds(self):
        service, _, _ = self.service([], {})
        company = company_for_name("示例芯片股份有限公司").model_copy(update={
            "canonical_name": "示例芯片股份有限公司",
            "resolution_status": "resolved",
        })
        alumni = page(
            "https://alumni.university.edu/company/123",
            title="校友企业介绍",
            body="示例芯片股份有限公司公司网址：www.example-chip.com。",
        )
        service._enrich_official_website(company, [alumni], 1)
        service._enrich_official_website(company, [alumni], 2)
        self.assertIsNone(company.official_website)
        self.assertEqual(
            service.last_execution_trace["official_website_external_candidate_hosts"],
            ["example-chip.com"],
        )

        legal = page(
            "https://www.example-chip.com/legal",
            title="法律声明",
            body="示例芯片股份有限公司运营的官方网站（https://www.example-chip.com/，以下简称本网站）。",
        )
        resolved = service._enrich_official_website(company, [legal], 3)
        self.assertEqual(resolved.official_website, "https://www.example-chip.com/")
        self.assertEqual(
            service.last_execution_trace["official_website_external_candidate_hosts"],
            ["example-chip.com"],
        )
        self.assertEqual(
            service.last_execution_trace["official_website_strong_candidate_hosts"],
            ["example-chip.com"],
        )

    async def test_repeated_query_is_removed_before_search(self):
        query = "某科技公司 官网"
        service, _, search = self.service(
            [
                initial_plan((query, "company_identity")),
                next_plan([(query, "company_identity")], terms=[], gaps=["产品"]),
            ],
            {query: [page("https://example.test/company")]},
        )
        result = await service.run("某科技公司", max_rounds=3)
        self.assertEqual([call.query for call in search.calls], [query])
        self.assertEqual(result.stop_reason, "no_grounded_followup_queries")
        self.assertEqual(result.trace[-1].stop_reason, "no_grounded_followup_queries")

    async def test_same_url_from_different_queries_is_ingested_once(self):
        q1, q2 = "某科技公司 官网", "某科技公司 产品"
        short_hit = SearchResult(
            title="简短结果",
            url="https://example.test/shared",
            content="摘要 X1芯片",
            provider="fake",
        )
        full_hit = page(
            "https://EXAMPLE.test/shared/",
            body="丰富正文提到 X1芯片经过客户验证，并由张三团队完成可靠性测试。",
        )
        service, _, _ = self.service(
            [initial_plan((q1, "company_identity"), (q2, "product"))],
            {q1: [short_hit], q2: [full_hit]},
        )
        result = await service.run("某科技公司", max_rounds=2, max_queries_per_round=2)
        self.assertEqual(result.sources_found, 1)
        self.assertEqual(result.sources_ingested, 1)
        self.assertEqual(result.trace[-1].duplicate_source_count, 1)
        self.assertEqual(self.kb.repository.counts()["sources"], 1)
        source_id = source_id_for(
            SourceType.WEB, canonical_url="https://example.test/shared"
        )
        version = self.kb.repository.get_current_source_version(source_id)
        chunks = self.kb.repository.list_chunks_for_version(version.source_version_id)
        self.assertTrue(any("丰富正文" in chunk.text for chunk in chunks))

    async def test_repeated_same_page_content_does_not_create_a_version(self):
        query = "某科技公司 技术"
        for _ in range(2):
            service, _, _ = self.service(
                [initial_plan((query, "technology"))],
                {query: [page("https://example.test/repeat")]},
            )
            result = await service.run("某科技公司", max_rounds=1)
        self.assertEqual(result.new_versions, 0)
        self.assertEqual(result.duplicate_versions, 1)
        self.assertEqual(self.kb.repository.counts()["source_versions"], 1)

    async def test_same_url_new_body_creates_a_new_current_version(self):
        query = "某科技公司 技术"
        service_a, _, _ = self.service(
            [initial_plan((query, "technology"))],
            {query: [page("https://example.test/update", body="旧版本包含 X1芯片信息。")]},
        )
        await service_a.run("某科技公司", max_rounds=1)
        service_b, _, _ = self.service(
            [initial_plan((query, "technology"))],
            {query: [page("https://example.test/update", body="新版本包含 X2芯片升级信息。") ]},
        )
        result = await service_b.run("某科技公司", max_rounds=1)
        counts = self.kb.repository.counts()
        self.assertEqual(counts["sources"], 1)
        self.assertEqual(counts["source_versions"], 2)
        self.assertEqual(counts["current_versions"], 1)
        self.assertEqual(result.new_versions, 1)

    async def test_url_and_citation_survive_search_kb_round_trip(self):
        query = "某科技公司 技术"
        result_page = page("https://example.test/provenance", body="某科技公司 X1芯片使用 RISC-V 架构。")
        service, _, _ = self.service(
            [initial_plan((query, "technology"))], {query: [result_page]}
        )
        await service.run("某科技公司", max_rounds=1)
        found = await self.kb.search(
            KnowledgeSearchRequest(
                query="X1芯片 RISC-V", top_k=10, knowledge_layers=[KnowledgeLayer.GENERAL]
            )
        )
        self.assertTrue(found)
        citation = found[0].chunk.citation
        self.assertEqual(citation.source_url, "https://example.test/provenance")
        self.assertEqual(citation.source_id, found[0].source.source_id)
        self.assertEqual(citation.source_version_id, found[0].source_version.source_version_id)
        self.assertEqual(citation.source_title, "技术资料")
        self.assertEqual(citation.locator.heading, "核心技术")
        self.assertTrue(citation.excerpt)

    async def test_max_rounds_is_a_hard_stop(self):
        q1, q2 = "某科技公司 技术", "某科技公司 X1芯片 专利"
        service, _, search = self.service(
            [
                initial_plan((q1, "technology")),
                next_plan([(q2, "technology")], terms=["X1芯片"], gaps=["专利"]),
            ],
            {q1: [page("https://example.test/max1")], q2: [page("https://example.test/max2")]},
        )
        result = await service.run("某科技公司", max_rounds=2)
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(result.stop_reason, "max_rounds")

    async def test_planner_stop_prevents_an_additional_round(self):
        q1, q2 = "某科技公司 技术", "某科技公司 X1芯片 认证"
        service, _, search = self.service(
            [
                initial_plan((q1, "technology")),
                next_plan([(q2, "technology")], terms=["X1芯片"], gaps=["认证"]),
                next_plan(terms=["X1芯片"], gaps=["认证"], should_continue=False),
            ],
            {q1: [page("https://example.test/stop1")], q2: [page("https://example.test/stop2")]},
        )
        result = await service.run("某科技公司", max_rounds=4)
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(result.stop_reason, "planner_stopped")

    async def test_no_information_gain_stops_after_configured_empty_gain_round(self):
        q1, q2 = "某科技公司 技术", "某科技公司 X1芯片 进展"
        shared_page = page("https://example.test/no-gain")
        service, _, search = self.service(
            [
                initial_plan((q1, "technology")),
                next_plan([(q2, "technology")], terms=["X1芯片"], gaps=["产业化"]),
                next_plan([(q2, "technology")], terms=["X1芯片"], gaps=["产业化"]),
            ],
            {q1: [shared_page], q2: [shared_page]},
        )
        result = await service.run("某科技公司", max_rounds=4)
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(result.stop_reason, "no_information_gain")

    async def test_max_source_limit_is_hard(self):
        query = "某科技公司 官网"
        service, _, search = self.service(
            [initial_plan((query, "company_identity"))],
            {
                query: [
                    page("https://example.test/max-source-a"),
                    page("https://example.test/max-source-b"),
                ]
            },
        )
        result = await service.run("某科技公司", max_rounds=3, max_sources=1)
        self.assertEqual(len(search.calls), 1)
        self.assertEqual(result.sources_found, 1)
        self.assertEqual(result.stop_reason, "max_sources")

    async def test_snippet_only_content_is_marked_and_extract_is_attempted(self):
        query = "某科技公司 官网"
        snippet = SearchResult(
            title="短摘要",
            url="https://example.test/snippet",
            content="搜索引擎返回的短摘要 X1芯片",
            provider="fake",
        )
        provider = FakeSearchProvider(
            {query: [snippet]},
            extracted={"https://example.test/snippet": "# 产品\n\n" + ("完整正文 X1芯片。" * 90)},
        )
        model = FakeStructuredModel([initial_plan((query, "company_identity"))])
        service = IterativeResearchService(RetrievalPlanner(model), provider, self.kb, entity_resolver=TestIdentityResolver(), relevance_gate=TestRelevanceGate())
        await service.run("某科技公司", max_rounds=1)
        self.assertEqual(provider.extract_calls, [["https://example.test/snippet"]])
        source_id = source_id_for(
            SourceType.WEB, canonical_url="https://example.test/snippet"
        )
        version = self.kb.repository.get_current_source_version(source_id)
        chunk = self.kb.repository.list_chunks_for_version(version.source_version_id)[0]
        self.assertEqual(chunk.metadata["content_scope"], "full_content")

    async def test_unextractable_snippet_is_kept_with_restricted_scope_metadata(self):
        query = "某科技公司 官网"
        snippet = SearchResult(
            title="短摘要",
            url="https://example.test/snippet-only",
            content="仅有搜索摘要，包含 X1芯片。",
            provider="fake",
        )
        provider = FakeSearchProvider({query: [snippet]})
        model = FakeStructuredModel([initial_plan((query, "company_identity"))])
        service = IterativeResearchService(RetrievalPlanner(model), provider, self.kb, entity_resolver=TestIdentityResolver(), relevance_gate=TestRelevanceGate())
        await service.run("某科技公司", max_rounds=1)
        source_id = source_id_for(
            SourceType.WEB, canonical_url="https://example.test/snippet-only"
        )
        version = self.kb.repository.get_current_source_version(source_id)
        chunk = self.kb.repository.list_chunks_for_version(version.source_version_id)[0]
        self.assertEqual(chunk.metadata["content_scope"], "search_snippet")
        self.assertEqual(version.metadata["content_scope"], "search_snippet")

    async def test_extract_failure_is_recorded_and_snippet_scope_is_retained(self):
        query = "某科技公司 官网"
        snippet = SearchResult(
            title="短摘要",
            url="https://example.test/extract-failure",
            content="搜索摘要 X1芯片。",
            provider="fake",
        )

        class ExtractFailureProvider(FakeSearchProvider):
            async def extract(self, urls):
                raise SearchProviderError("timeout", "fake timeout")

        provider = ExtractFailureProvider({query: [snippet]})
        model = FakeStructuredModel([initial_plan((query, "company_identity"))])
        service = IterativeResearchService(RetrievalPlanner(model), provider, self.kb, entity_resolver=TestIdentityResolver(), relevance_gate=TestRelevanceGate())
        result = await service.run("某科技公司", max_rounds=1)
        self.assertEqual(result.trace[0].content_retrieval_failures, ["timeout"])
        self.assertEqual(result.warnings, [])

    def test_web_chunker_keeps_heading_and_bounds_chunk_size(self):
        chunker = WebContentChunker(max_chars=320, overlap_chars=30)
        body = "。".join(["X1芯片完成阶段验证并继续开展工程测试" for _ in range(35)])
        chunks = chunker.split(f"# 技术进展\n\n{body}")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(section == "技术进展" for section, _, _ in chunks))
        self.assertTrue(all(len(text) <= 320 for _, _, text in chunks))
        self.assertTrue(all("技术进展" in text for _, _, text in chunks))

    async def test_invalid_planner_shape_gets_one_structure_repair(self):
        query = "某科技公司 官网"
        service, model, search = self.service(
            [
                {"reason": "malformed"},
                initial_plan((query, "company_identity")),
            ],
            {query: []},
        )
        await service.run("某科技公司", max_rounds=1)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(search.calls), 1)
        self.assertIn("invalid_prior_output", model.calls[1]["payload"])

    def test_tavily_missing_key_is_categorized_without_network_call(self):
        with self.assertRaises(SearchProviderError) as raised:
            TavilySearchProvider("")
        self.assertEqual(raised.exception.category, "provider_not_configured")

    async def test_tavily_maps_search_response_without_logging_credentials(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "results": [
                            {
                                "title": "官方技术页面",
                                "url": "https://example.test/tech",
                                "content": "技术摘要",
                                "raw_content": "正文内容",
                                "score": 0.92,
                                "published_date": "2026-09-01",
                            }
                        ]
                    }
                ).encode("utf-8")

        provider = TavilySearchProvider("test-secret", timeout=1)
        with patch("app.research.tavily_provider.urlopen", return_value=FakeResponse()) as opened:
            results = await provider.search(SearchRequest(query="测试企业技术", max_results=3))
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["api_key"], "test-secret")
        self.assertEqual(results[0].provider, "tavily")
        self.assertEqual(results[0].raw_content, "正文内容")
        self.assertEqual(results[0].published_at.date().isoformat(), "2026-09-01")

    def test_tavily_maps_provider_http_and_network_failures(self):
        provider = TavilySearchProvider("test-secret", timeout=1)
        cases = [
            (HTTPError("https://api.tavily.com/search", 401, "unauthorized", {}, BytesIO()), "authentication"),
            (HTTPError("https://api.tavily.com/search", 429, "rate limited", {}, BytesIO()), "rate_limit"),
            (TimeoutError("slow"), "timeout"),
            (URLError("offline"), "network"),
        ]
        for failure, category in cases:
            with self.subTest(category=category), patch(
                "app.research.tavily_provider.urlopen", side_effect=failure
            ):
                with self.assertRaises(SearchProviderError) as raised:
                    provider._post_sync(
                        "https://api.tavily.com/search", {"api_key": "test-secret"}
                    )
                self.assertEqual(raised.exception.category, category)

    def test_tavily_invalid_json_is_categorized(self):
        class InvalidResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"not-json"

        provider = TavilySearchProvider("test-secret", timeout=1)
        with patch("app.research.tavily_provider.urlopen", return_value=InvalidResponse()):
            with self.assertRaises(SearchProviderError) as raised:
                provider._post_sync("https://api.tavily.com/search", {})
        self.assertEqual(raised.exception.category, "invalid_response")



if __name__ == "__main__":
    unittest.main()
