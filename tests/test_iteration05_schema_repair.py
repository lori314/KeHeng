import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.technology_agent import TechnologyAgent
from app.llm import LLMContextChunk, LLMExtractionRequest, LLMExtractionResult, OpenAICompatibleProvider
from app.llm.api_model import OpenAICompatibleHTTPTransport
from app.llm.provider import LLMProviderError
from app.rag.retrieval import RetrievedEvidence
from app.services.analysis_service import _safe_module_failure


class Iteration05SchemaRepairTests(unittest.TestCase):
    def test_explicit_insufficient_evidence_summary_is_accepted_without_repair(self):
        empty_refs = {
            "technology_summary": "证据不足：当前提供的检索片段无法支持技术事实摘要。",
            "summary_status": "insufficient_evidence",
            "summary_evidence_ids": [],
            "technology_indicators": {}, "strengths": [], "risks": [],
        }
        calls = []

        async def transport(_endpoint, _model, request):
            calls.append(request)
            return empty_refs

        provider = OpenAICompatibleProvider("unused", "unused", transport=transport)
        result = asyncio.run(provider.extract(LLMExtractionRequest(task_id="task", enterprise_name="synthetic", chunks=[], prompt="prompt")))
        self.assertEqual(result.summary_status, "insufficient_evidence")
        self.assertEqual(result.summary_evidence_ids, [])
        self.assertEqual(len(calls), 1)

    def test_repair_cannot_make_invalid_citations_or_system_failure_look_like_insufficient_evidence(self):
        invalid = LLMExtractionResult(
            technology_summary="研发投入[E99]", summary_status="supported", summary_evidence_ids=["E99"],
            technology_indicators={}, strengths=[], risks=[],
        )
        evidence = [RetrievedEvidence(task_id="task", document_id="doc", document_name="synthetic.pdf", page_number=1, chunk_id="c1", text="输入证据", locator="p.1", score=1.0)]
        agent = TechnologyAgent(retriever=None)  # conversion boundary only; no retrieval or model call
        with self.assertRaisesRegex(ValueError, "unknown evidence") as caught:
            agent._convert_llm_result(invalid, evidence, {"c1": "E1"})
        failure = _safe_module_failure(caught.exception)
        self.assertEqual(failure["category"], "schema_failure")
        self.assertIn("validation_errors", failure)
        self.assertNotIn("insufficient_evidence", failure["category"])

    def test_explicit_refusal_is_valid_but_factual_summary_without_refs_is_rejected(self):
        valid = LLMExtractionResult(
            technology_summary="摘要[E1]", summary_status="supported", summary_evidence_ids=["E1"],
            technology_indicators={}, strengths=[], risks=[],
        )
        self.assertEqual(valid.summary_evidence_ids, ["E1"])
        with self.assertRaises(Exception):
            LLMExtractionResult(
                technology_summary="摘要", summary_status="supported",
                technology_indicators={}, strengths=[], risks=[],
            )
        no_evidence_refusal = LLMExtractionResult(
            technology_summary="证据不足：当前提供的检索片段无法支持技术事实摘要。",
            summary_status="insufficient_evidence", summary_evidence_ids=[],
            technology_indicators={}, strengths=[], risks=[],
        )
        self.assertEqual(no_evidence_refusal.summary_evidence_ids, [])
        evidence = [RetrievedEvidence(task_id="task", document_id="doc", document_name="synthetic.pdf", page_number=1, chunk_id="c1", text="输入证据", locator="p.1", score=1.0)]
        agent = TechnologyAgent(retriever=None)
        accepted = agent._convert_llm_result(no_evidence_refusal, evidence, {"c1": "E1"})
        self.assertEqual(accepted.summary_status, "insufficient_evidence")
        factual_no_ref = LLMExtractionResult(
            technology_summary="企业拥有专利。", summary_status="supported",
            summary_evidence_ids=[], technology_indicators={}, strengths=[], risks=[],
        )
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            agent._convert_llm_result(factual_no_ref, evidence, {"c1": "E1"})
        falsely_empty = LLMExtractionResult(
            technology_summary="企业已形成规模化专利资产。",
            summary_status="insufficient_evidence", summary_evidence_ids=[],
            technology_indicators={}, strengths=[], risks=[],
        )
        with self.assertRaisesRegex(ValueError, "exact refusal text"):
            agent._convert_llm_result(falsely_empty, evidence, {"c1": "E1"})

    def test_saved_request_history_matches_sent_excerpt_and_assembly_audit(self):
        transport = OpenAICompatibleHTTPTransport("test-only", max_retries=0)
        response = {
            "choices": [{"message": {"content": '{"technology_summary":"摘要[E1]","summary_status":"supported","summary_evidence_ids":["E1"],"technology_indicators":{},"strengths":[],"risks":[]}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }
        transport._request = lambda _endpoint, _payload: response
        request = LLMExtractionRequest(
            task_id="task", enterprise_name="synthetic", prompt="prompt",
            chunks=[LLMContextChunk(evidence_id="E1", document_name="synthetic.pdf", page_number=1, chunk_id="chunk1", excerpt="完整支持句。", retrieval_score=0.8)],
            context_assembly={"strategy": "balanced_sentences_v1", "budget_unit": "characters", "selected_characters": 6},
        )
        asyncio.run(transport("https://example.invalid", "offline-test", request))
        saved = transport.request_history[0]
        self.assertEqual(saved["chunks"][0]["excerpt"], "完整支持句。")
        self.assertEqual(saved["context_assembly"], request.context_assembly)


if __name__ == "__main__":
    unittest.main()
