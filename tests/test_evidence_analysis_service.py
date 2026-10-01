"""Offline tests for production V2 orchestration and task API contracts."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.api.routes.evidence_analysis import router as evidence_analysis_router  # noqa: E402
from app.main import app as main_app  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.knowledge.identity import company_for_name  # noqa: E402
from app.report_v2 import EvidenceFirstReportAssembler  # noqa: E402
from app.research.contracts import IterativeResearchResult  # noqa: E402
from app.services.evidence_analysis_service import (  # noqa: E402
    EvidenceAnalysisService,
    EvidenceAnalysisServiceError,
)
from app.services.evidence_analysis_tasks import (  # noqa: E402
    EvidenceAnalysisTaskStore,
    EvidenceAnalysisTaskManager,
    get_evidence_analysis_task_manager,
)
from tests.test_report_v2 import MemoryRepository, make_profiles  # noqa: E402


class FakeKnowledgeBase:
    def __init__(self, repository):
        self.repository = repository
        self.closed = False

    def close(self):
        self.closed = True


class FakeResearchService:
    def __init__(self, _planner, _search, _kb, *, status="resolved"):
        self.status = status
        self.last_execution_trace = {"current_stage": "research_completed", "identity_results_count": 2}

    async def run(self, enterprise_name, **kwargs):
        self.params = kwargs
        return IterativeResearchResult(
            enterprise_name=enterprise_name, rounds=1, queries_executed=["公开资料检索"],
            sources_found=2, sources_ingested=2, new_versions=2,
            duplicate_versions=0, knowledge_chunks_added=4, trace=[],
            remaining_information_gaps=[], stop_reason="test_complete",
            entity_resolution_status=self.status,
            resolved_canonical_name=enterprise_name if self.status == "resolved" else None,
        )


class ProfileProcessor:
    def __init__(self, profile):
        self.profile = profile
        self.last_execution_trace = {"status": "complete"}

    async def process_company(self, _company_id):
        return self.profile


class AsyncAssertionProcessor:
    def __init__(self, profile):
        self.profile = profile
        self.last_execution_trace = {"status": "complete"}

    def process_company(self, _company_id):
        return self.profile


class EvidenceAnalysisServiceTests(unittest.TestCase):
    def _service(self, repository, kb, *, research_status="resolved", semantic_processor_factory=None):
        return EvidenceAnalysisService(
            runtime_root="unused", settings=Settings(), model=object(), search_provider=object(),
            knowledge_base_factory=lambda _root: kb,
            research_service_factory=lambda planner, search, active_kb: FakeResearchService(planner, search, active_kb, status=research_status),
            semantic_processor_factory=semantic_processor_factory or (lambda _model, _kb: ProfileProcessor(repository.tech)),
            finance_processor_factory=lambda _model, _kb: ProfileProcessor(repository.finance),
            assertion_processor_factory=lambda _kb: AsyncAssertionProcessor(repository.assertions),
        )

    def test_full_service_pipeline_returns_linked_persisted_profiles_and_report(self):
        cid = company_for_name("示例科技").company_id
        repo = MemoryRepository(cid)
        make_profiles(repo)
        kb = FakeKnowledgeBase(repo)
        service = self._service(repo, kb)
        events = []
        async def run():
            result = await service.analyze_company("示例科技", progress_callback=lambda stage, metadata: events.append((stage, metadata)))
            return result, service.last_execution_details

        result, details = asyncio.run(run())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result_status, "completed")
        self.assertEqual(result.company_id, cid)
        self.assertEqual(result.report.company_id, cid)
        self.assertEqual(result.technology_profile_id, "tech-1")
        self.assertEqual(result.finance_profile_id, "fin-1")
        self.assertEqual(result.assertion_profile_id, "assert-1")
        self.assertEqual(result.research.sources_ingested, 2)
        self.assertEqual([stage for stage, _ in events], ["research", "research", "technology_semantic", "technology_semantic", "technology_finance", "technology_finance", "evidence_assertions", "evidence_assertions", "evidence_first_report", "complete"])
        self.assertTrue(kb.closed)
        self.assertNotIn("identity_candidates", result.model_dump(mode="json")["research"])
        self.assertEqual(details["research_service"].params, {
            "max_rounds": 3, "max_queries_per_round": 5, "max_results_per_query": 5,
        })

    def test_unresolved_entity_skips_all_downstream_processors(self):
        cid = company_for_name("示例科技").company_id
        repo = MemoryRepository(cid)
        make_profiles(repo)
        calls = []
        kb = FakeKnowledgeBase(repo)
        service = self._service(repo, kb, research_status="ambiguous")
        service._finance_processor_factory = lambda *_: calls.append("finance")
        service._assertion_processor_factory = lambda *_: calls.append("assertions")
        service._report_assembler_factory = lambda *_: calls.append("report")
        result = asyncio.run(service.analyze_company("示例科技"))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result_status, "entity_not_resolved")
        self.assertIsNone(result.report)
        self.assertEqual(calls, [])
        self.assertTrue(kb.closed)

    def test_task_store_exposes_queued_and_research_processing_stages(self):
        async def scenario():
            store = EvidenceAnalysisTaskStore()
            await store.create("task-1", "示例企业")
            initial = await store.get("task-1")
            await store.progress("task-1", "research")
            current = await store.get("task-1")
            return initial, current

        initial, current = asyncio.run(scenario())
        self.assertEqual((initial.status, initial.current_stage), ("processing", "queued"))
        self.assertEqual((current.status, current.current_stage), ("processing", "research"))

    def test_company_lock_is_shared_per_company_within_event_loop(self):
        async def scenario():
            same_first = EvidenceAnalysisService._company_lock("same-company")
            same_second = EvidenceAnalysisService._company_lock("same-company")
            other = EvidenceAnalysisService._company_lock("other-company")
            return same_first, same_second, other

        first, second, other = asyncio.run(scenario())
        self.assertIs(first, second)
        self.assertIsNot(first, other)

    def test_stage_failure_has_safe_stage_category_and_closes_kb(self):
        cid = company_for_name("示例科技").company_id
        repo = MemoryRepository(cid)
        make_profiles(repo)
        kb = FakeKnowledgeBase(repo)

        class FailingProcessor:
            last_execution_trace = {"semantic_substage": "extract"}

            async def process_company(self, _company_id):
                raise RuntimeError("raw payload must not surface")

        service = self._service(repo, kb, semantic_processor_factory=lambda *_: FailingProcessor())
        with self.assertRaises(EvidenceAnalysisServiceError) as caught:
            asyncio.run(service.analyze_company("示例科技"))
        self.assertEqual(caught.exception.stage, "technology_semantic")
        self.assertEqual(caught.exception.category, "RuntimeError")
        self.assertNotIn("raw payload", caught.exception.message)
        self.assertTrue(kb.closed)


class EvidenceAnalysisApiTests(unittest.TestCase):
    def test_openapi_keeps_v2_and_legacy_routes(self):
        paths = main_app.openapi()["paths"]
        self.assertIn("/api/evidence-analysis", paths)
        self.assertIn("/api/evidence-analysis/{task_id}", paths)
        self.assertIn("/api/report/v2/company/{company_id}", paths)
        self.assertIn("/analysis/create", paths)
        self.assertIn("/analysis/{task_id}", paths)

    def test_blank_name_returns_stable_422_code(self):
        app = _minimal_app()
        client = TestClient(app)
        response = client.post("/api/evidence-analysis", json={"enterprise_name": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "blank_enterprise_name")

    def test_missing_providers_returns_503_categories_only(self):
        app = _minimal_app()
        manager = EvidenceAnalysisTaskManager(EvidenceAnalysisService(settings=Settings(
            llm_endpoint="", llm_model="", llm_api_key="",
            web_search_provider="", tavily_api_key="",
        )))
        app.dependency_overrides[get_evidence_analysis_task_manager] = lambda: manager
        client = TestClient(app)
        response = client.post("/api/evidence-analysis", json={"enterprise_name": "示例企业"})
        self.assertEqual(response.status_code, 503)
        data = response.json()["detail"]
        self.assertEqual(data["code"], "provider_not_configured")
        self.assertEqual(data["missing_providers"], ["LLM", "Tavily"])
        self.assertNotIn(".env", response.text)
        self.assertNotIn("Authorization", response.text)

    def test_background_post_poll_returns_completed_report_and_company_id(self):
        cid = company_for_name("示例科技").company_id
        repo = MemoryRepository(cid)
        make_profiles(repo)
        report = EvidenceFirstReportAssembler(repo).build(cid)

        class FakeTaskService:
            def require_providers(self):
                return None

            async def analyze_company(self, enterprise_name, *, progress_callback=None):
                if progress_callback:
                    await progress_callback("research", {})
                from app.services.evidence_analysis_service import EvidenceAnalysisResult, ResearchSummary
                return EvidenceAnalysisResult(
                    enterprise_name=enterprise_name, status="completed", result_status="completed",
                    company_id=cid, canonical_name="示例科技",
                    research=ResearchSummary(rounds=1, sources_found=1, sources_ingested=1, knowledge_chunks_added=1, stop_reason="done", entity_resolution_status="resolved", official_website=None),
                    technology_profile_id="tech-1", finance_profile_id="fin-1", assertion_profile_id="assert-1", report=report,
                )

        app = _minimal_app()
        manager = EvidenceAnalysisTaskManager(FakeTaskService())
        app.dependency_overrides[get_evidence_analysis_task_manager] = lambda: manager
        client = TestClient(app)
        created = client.post("/api/evidence-analysis", json={"enterprise_name": " 示例科技 "})
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        task = client.get(f"/api/evidence-analysis/{task_id}")
        self.assertEqual(task.status_code, 200)
        body = task.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["result_status"], "completed")
        self.assertEqual(body["company_id"], cid)
        self.assertEqual(body["report"]["company_id"], cid)
        self.assertEqual(body["report_url"], f"/api/report/v2/company/{cid}")


def _minimal_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(evidence_analysis_router)
    return app


if __name__ == "__main__":
    unittest.main()
