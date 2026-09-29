"""v0.6 provider, mode-switching, and evidence-contract tests."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.llm import (
    LLMContextChunk,
    LLMExtractionRequest,
    LLMExtractionResult,
    MockLLMProvider,
    OpenAICompatibleProvider,
)
from evaluation.compare_agent_modes import compare
from app.llm.provider import LLMProviderError
from app.llm.provider import LLMProvider
from app.services.analysis_service import TechnologyAnalysisInput, TechnologyAssessmentService
from app.services.analysis_service import AnalysisServiceError


ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "data" / "evaluation_cases"


class LLMExtractionTest(unittest.TestCase):
    def test_mock_contract_has_no_final_score_and_uses_valid_evidence(self) -> None:
        request = LLMExtractionRequest(
            task_id="task-1",
            enterprise_name="示例企业",
            prompt="test",
            chunks=[
                LLMContextChunk(
                    evidence_id="E1",
                    document_name="profile.pdf",
                    page_number=1,
                    chunk_id="chunk-1",
                    excerpt="企业自主研发核心算法，已获得授权专利。",
                    retrieval_score=0.9,
                )
            ],
        )
        result = asyncio.run(MockLLMProvider().extract(request))
        payload = result.model_dump()
        self.assertNotIn("technology_score", payload)
        self.assertEqual(set(payload["technology_indicators"]), {
            "technical_autonomy",
            "innovation_capability",
            "intellectual_property",
            "technical_maturity",
        })
        evidence_ids = set(payload["summary_evidence_ids"])
        for item in payload["technology_indicators"].values():
            evidence_ids.update(item["evidence_ids"])
        self.assertTrue(evidence_ids <= {"E1"})

    def test_api_adapter_uses_injected_transport_only(self) -> None:
        request = LLMExtractionRequest(
            task_id="task-1",
            enterprise_name="示例企业",
            prompt="test",
            chunks=[],
        )
        expected = LLMExtractionResult(
            technology_summary="摘要",
            summary_status="supported",
            summary_evidence_ids=["E1"],
            technology_indicators={},
        )
        calls: list[tuple[str, str]] = []

        async def transport(endpoint: str, model: str, _request: object) -> dict:
            calls.append((endpoint, model))
            return expected.model_dump()

        provider = OpenAICompatibleProvider(
            "https://example.invalid", "demo", transport=transport
        )
        result = asyncio.run(provider.extract(request))
        self.assertEqual(result.technology_summary, "摘要")
        self.assertEqual(calls, [("https://example.invalid", "demo")])

        with self.assertRaises(LLMProviderError):
            asyncio.run(OpenAICompatibleProvider("unused", "unused").extract(request))

    def test_rule_and_llm_modes_match_all_fixed_cases(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as runtime:
            result = asyncio.run(compare(CASE_ROOT, runtime))
        self.assertEqual(result["rule"]["failed_cases"], [])
        self.assertEqual(result["llm"]["failed_cases"], [])
        self.assertEqual(
            result["comparison"]["indicator_expectation_match_rate"]["delta_percentage_points"],
            0.0,
        )
        self.assertEqual(
            result["comparison"]["evidence_reference_completeness_rate"]["llm"],
            100.0,
        )

    def test_fallback_mode_returns_rule_result_when_provider_fails(self) -> None:
        class BrokenProvider(LLMProvider):
            name = "broken"
            implementation_version = "test"

            async def extract(self, _request: object) -> LLMExtractionResult:
                raise LLMProviderError("simulated provider outage")

        case = CASE_ROOT / "case_001"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as runtime:
            service = TechnologyAssessmentService(
                project_root=ROOT,
                runtime_root=runtime,
                agent_mode="fallback",
                llm_provider=BrokenProvider(),
            )
            artifacts = asyncio.run(
                service.run_with_artifacts(
                    TechnologyAnalysisInput(
                        task_id="fallback-case-001",
                        enterprise_name="卓越感知科技有限公司",
                        pdf_path=case / "company_profile.pdf",
                        original_file_name="company_profile.pdf",
                    )
                )
            )
        self.assertEqual(artifacts.evaluation_result.technology_score, 82.75)

    def test_llm_mode_without_provider_fails_without_mock_or_rule_fallback(self) -> None:
        case = CASE_ROOT / "case_001"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as runtime:
            service = TechnologyAssessmentService(
                project_root=ROOT,
                runtime_root=runtime,
                agent_mode="llm",
            )
            self.assertIsNone(service._llm_provider)
            with self.assertRaises(AnalysisServiceError) as context:
                asyncio.run(
                    service.run_with_artifacts(
                        TechnologyAnalysisInput(
                            task_id="llm-no-provider-001",
                            enterprise_name="卓越感知科技有限公司",
                            pdf_path=case / "company_profile.pdf",
                            original_file_name="company_profile.pdf",
                        )
                    )
                )
        self.assertEqual(context.exception.code, "llm_extraction_failed")
        self.assertIn("未自动切换", context.exception.message)

    def test_llm_provider_failure_is_explicitly_reported(self) -> None:
        case = CASE_ROOT / "case_001"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as runtime:
            service = TechnologyAssessmentService(
                project_root=ROOT,
                runtime_root=runtime,
                agent_mode="llm",
                llm_provider=OpenAICompatibleProvider("not-configured", "not-configured"),
            )
            with self.assertRaises(AnalysisServiceError) as context:
                asyncio.run(
                    service.run_with_artifacts(
                        TechnologyAnalysisInput(
                            task_id="llm-provider-failure-001",
                            enterprise_name="卓越感知科技有限公司",
                            pdf_path=case / "company_profile.pdf",
                            original_file_name="company_profile.pdf",
                        )
                    )
                )
        self.assertEqual(context.exception.code, "llm_extraction_failed")


if __name__ == "__main__":
    unittest.main()
