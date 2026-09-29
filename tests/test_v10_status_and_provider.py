"""v1.0 retry, Industry LLM contract, and composite status tests."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.industry_agent import IndustryAgent, IndustryAgentMode, IndustryAgentRequest  # noqa: E402
from app.llm import (  # noqa: E402
    LLMContextChunk,
    LLMExtractionRequest,
    LLMIndustryExtractionResult,
    LLMIndicatorExtraction,
    LLMProvider,
    OpenAICompatibleHTTPTransport,
)
from app.rag.retrieval import RetrievedEvidence  # noqa: E402
from evaluation.composite_scoring import ComprehensiveEvaluationEngine  # noqa: E402


class FixedRetriever:
    def __init__(self, items):
        self.items = items

    async def search(self, _request):
        return self.items


class IndustryProvider(LLMProvider):
    async def extract(self, request):
        raise AssertionError("technology extraction is not expected")

    async def extract_industry(self, request):
        return LLMIndustryExtractionResult(
            industry_indicators={
                key: LLMIndicatorExtraction(score=70, evidence_ids=["E1"], rationale="证据支持")
                for key in ("market_potential", "industry_growth", "competitive_position", "policy_environment")
            }
        )


class V10Test(unittest.TestCase):
    def test_provider_performs_at_most_one_format_repair(self):
        class RepairTransport:
            def __init__(self):
                self.calls = 0
                self.last_observation = {}
                self.last_content = None

            async def __call__(self, _endpoint, _model, _request):
                self.calls += 1
                if self.calls == 1:
                    from app.llm.provider import LLMProviderError
                    self.last_content = "```json\n{bad}\n```"
                    raise LLMProviderError("invalid JSON", category="json_syntax_error")
                return {
                    "technology_summary": "摘要",
                    "summary_status": "supported",
                    "summary_evidence_ids": ["E1"],
                    "technology_indicators": {},
                    "strengths": [],
                    "risks": [],
                }

            def mark_schema_success(self):
                pass

            def mark_schema_failure(self, _category="schema_failure"):
                pass

            def mark_repair(self, **values):
                self.last_observation.update(values)

        transport = RepairTransport()
        provider = __import__("app.llm.api_model", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider("unused", "model", transport=transport)
        request = LLMExtractionRequest(task_id="repair", enterprise_name="企业", chunks=[], prompt="json")
        result = asyncio.run(provider.extract(request))
        self.assertEqual(result.technology_summary, "摘要")
        self.assertEqual(transport.calls, 2)
    def test_transport_retries_transient_http_failure(self):
        transport = OpenAICompatibleHTTPTransport("secret", max_retries=2, backoff_base=0)
        calls = {"n": 0}

        def fake_request(_endpoint, _payload):
            calls["n"] += 1
            if calls["n"] == 1:
                from app.llm.provider import LLMProviderError
                raise LLMProviderError("temporary", category="http_5xx", retryable=True)
            return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}

        transport._request = fake_request
        request = LLMExtractionRequest(
            task_id="retry", enterprise_name="企业", chunks=[
                LLMContextChunk(evidence_id="E1", document_name="a", page_number=1, chunk_id="c", excerpt="证据", retrieval_score=1)
            ], prompt="json",
        )
        result = asyncio.run(transport("http://unused", "model", request))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(transport.last_observation["retry_count"], 1)
        self.assertFalse(transport.last_observation["first_attempt_success"])
        self.assertTrue(transport.last_observation["eventual_success"])

    def test_transport_json_mode_can_be_disabled(self):
        transport = OpenAICompatibleHTTPTransport("secret", json_mode=False)
        captured = {}

        def fake_request(_endpoint, payload):
            captured.update(payload)
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

        transport._request = fake_request
        request = LLMExtractionRequest(task_id="json-mode", enterprise_name="企业", chunks=[], prompt="json")
        asyncio.run(transport("http://unused", "model", request))
        self.assertNotIn("response_format", captured)
        self.assertFalse(transport.last_observation["response_format_enabled"])

    def test_industry_llm_is_evidence_bound(self):
        item = RetrievedEvidence(task_id="t", document_id="d", document_name="a.pdf", page_number=1, chunk_id="c", text="企业已签约客户，行业增长，存在竞争优势和政策支持。", locator="p1", score=0.9)
        result = asyncio.run(
            IndustryAgent(FixedRetriever([item]), mode=IndustryAgentMode.LLM, llm_provider=IndustryProvider()).analyze(
                IndustryAgentRequest(task_id="t", evidence_id_start=1)
            )
        )
        self.assertEqual(result.industry_indicators.market_potential.score, 70)
        self.assertEqual(result.industry_indicators.market_potential.evidence, ["E1"])

    def test_composite_missing_dimension_is_not_renormalized(self):
        engine = ComprehensiveEvaluationEngine({"technology": 0.6, "industry": 0.4}, "test")
        result = engine.evaluate(
            {"technology_score": None, "evidence_mapping": []},
            {"industry_score": 80, "evidence_mapping": [{"indicator_id": "market_potential", "evidence_ids": ["E1"]}]},
        )
        self.assertEqual(result.assessment_status, "insufficient_evidence")
        self.assertIsNone(result.overall_score)
        self.assertEqual(result.evidence_coverage["technology"], 0.0)


if __name__ == "__main__":
    unittest.main()
