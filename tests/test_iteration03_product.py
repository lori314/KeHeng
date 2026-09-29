from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.llm.mock import MockLLMProvider  # noqa: E402
from app.llm.provider import LLMProviderError  # noqa: E402
from app.llm.provider import LLMExtractionResult, LLMIndicatorExtraction  # noqa: E402
from app.agents.technology_agent import TechnologyAgent  # noqa: E402
from app.rag.bm25 import BM25KnowledgeBase  # noqa: E402
from app.rag.document_parser import DocumentChunk  # noqa: E402
from app.rag.retrieval import RetrievalQuery  # noqa: E402
from app.rag.retrieval import RetrievedEvidence  # noqa: E402
from app.services.analysis_service import AnalysisServiceError, TechnologyAnalysisInput, TechnologyAssessmentService  # noqa: E402


SAMPLE_PDF = ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"


class ProductIteration03Tests(unittest.TestCase):
    def test_rule_product_runs_tech_only_with_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TechnologyAssessmentService(project_root=ROOT, runtime_root=Path(tmp), retrieval_mode="bm25")
            result = asyncio.run(service.run_product(TechnologyAnalysisInput(task_id="rule-a", enterprise_name="示例企业", pdf_path=SAMPLE_PDF, original_file_name=SAMPLE_PDF.name), "rule_demo"))
        self.assertEqual(result["run_info"]["actual_mode"], "rule_demo")
        self.assertEqual(result["modules"]["industry"]["status"], "not_run")
        self.assertEqual(result["modules"]["technology"]["status"], "completed")

    def test_real_mode_without_provider_config_fails_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TechnologyAssessmentService(project_root=ROOT, runtime_root=Path(tmp))
            with patch("app.services.analysis_service.get_settings", return_value=type("Settings", (), {"llm_endpoint": "", "llm_model": "", "llm_api_key": ""})()):
                with self.assertRaises(AnalysisServiceError) as caught:
                    asyncio.run(service.run_product(TechnologyAnalysisInput(task_id="missing-model", enterprise_name="示例企业", pdf_path=SAMPLE_PDF, original_file_name=SAMPLE_PDF.name), "real_model"))
        self.assertEqual(caught.exception.code, "model_not_configured")

    def test_two_module_success_and_one_module_failure_are_separately_retained(self) -> None:
        class TechFailProvider(MockLLMProvider):
            async def extract(self, request):
                raise LLMProviderError("test substitute failure", category="test_failure")

        with tempfile.TemporaryDirectory() as tmp:
            success = TechnologyAssessmentService(project_root=ROOT, runtime_root=Path(tmp) / "success", llm_provider=MockLLMProvider(), retrieval_mode="bm25")
            result = asyncio.run(success.run_product(TechnologyAnalysisInput(task_id="mock-ok", enterprise_name="示例企业", pdf_path=SAMPLE_PDF, original_file_name=SAMPLE_PDF.name), "real_model"))
            partial = TechnologyAssessmentService(project_root=ROOT, runtime_root=Path(tmp) / "partial", llm_provider=TechFailProvider(), retrieval_mode="bm25")
            partial_result = asyncio.run(partial.run_product(TechnologyAnalysisInput(task_id="mock-partial", enterprise_name="示例企业", pdf_path=SAMPLE_PDF, original_file_name=SAMPLE_PDF.name), "real_model"))
        self.assertEqual(result["result_status"], "completed")
        self.assertEqual(result["modules"]["industry"]["status"], "completed")
        self.assertEqual(partial_result["result_status"], "partial")
        self.assertEqual(partial_result["modules"]["technology"]["status"], "failed")
        self.assertEqual(partial_result["modules"]["industry"]["status"], "completed")
        self.assertIn("该状态不代表企业资料不足", partial_result["module_failures"]["technology"]["message"])

    def test_all_real_model_modules_failed_keeps_system_failure_status(self) -> None:
        class AllFailProvider(MockLLMProvider):
            async def extract(self, request):
                raise LLMProviderError("test substitute failure", category="test_failure")

            async def extract_industry(self, request):
                raise LLMProviderError("test substitute failure", category="test_failure")

        with tempfile.TemporaryDirectory() as tmp:
            service = TechnologyAssessmentService(project_root=ROOT, runtime_root=Path(tmp), llm_provider=AllFailProvider(), retrieval_mode="bm25")
            result = asyncio.run(service.run_product(TechnologyAnalysisInput(task_id="mock-fail", enterprise_name="示例企业", pdf_path=SAMPLE_PDF, original_file_name=SAMPLE_PDF.name), "real_model"))
        self.assertEqual(result["result_status"], "failed")
        self.assertIsNone(result["report"])
        self.assertEqual({item["status"] for item in result["modules"].values()}, {"failed"})

    def test_bm25_filters_other_task_evidence_and_deduplicates(self) -> None:
        async def run() -> list:
            store = BM25KnowledgeBase()
            await store.upsert([
                DocumentChunk(task_id="task-a", document_id="a", document_name="a.pdf", page_number=1, chunk_id="same", text="SMIC global foundry ranking", locator="p1", metadata={}),
                DocumentChunk(task_id="task-b", document_id="b", document_name="b.pdf", page_number=1, chunk_id="other", text="SMIC global foundry ranking confidential", locator="p1", metadata={}),
            ])
            return await store.query(RetrievalQuery(task_id="task-a", query="SMIC global foundry ranking", top_k=5))
        results = asyncio.run(run())
        self.assertEqual([item.task_id for item in results], ["task-a"])
        self.assertEqual(len({item.chunk_id for item in results}), len(results))

    def test_saved_smic_orphan_reference_is_not_counted_as_bound(self) -> None:
        from types import SimpleNamespace
        from evaluation.recompute_history import citation_audit

        source = ROOT / "data" / "real_cases" / "smic" / "sources" / "annual_report.pdf"
        analysis = __import__("json").loads((ROOT / "runtime" / "real_cases" / "smic" / "technology_analysis.json").read_text(encoding="utf-8"))
        audit = citation_audit(SimpleNamespace(pdf_path=source), {"artifacts": {"technology_analysis": analysis}})
        self.assertEqual(audit["free_text_ids"]["n"], 15)
        self.assertEqual(audit["free_text_ids"]["belong_to_task_domain_evidence"], 15)
        self.assertEqual(audit["free_text_ids"]["bound_to_declared_support_location"], 14)

    def test_agent_rejects_the_same_unbound_rationale_citation_shape(self) -> None:
        evidence = RetrievedEvidence(task_id="t", document_id="d", document_name="a.pdf", page_number=1, chunk_id="chunk-1", text="研发费用增长。", locator="p1", score=0.8)
        result = LLMExtractionResult(technology_summary="检索到信息[E1]", summary_status="supported", summary_evidence_ids=["E1"], technology_indicators={"innovation_capability": LLMIndicatorExtraction(score=None, evidence_ids=[], rationale="仍缺创新证据[E1]")})
        agent = TechnologyAgent.__new__(TechnologyAgent)
        with self.assertRaisesRegex(ValueError, "not bound"):
            agent._convert_llm_result(result, [evidence], {"chunk-1": "E1"})


if __name__ == "__main__":
    unittest.main()
