"""v0.7 Industry Agent, industry scoring, and comprehensive report tests."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.industry_agent import IndustryAgent, IndustryAgentRequest  # noqa: E402
from app.rag.retrieval import RetrievedEvidence  # noqa: E402
from app.report import (  # noqa: E402
    ComprehensiveReportGenerator,
    ComprehensiveReportRequest,
    EvaluationResultInput,
)
from evaluation.composite_scoring import ComprehensiveEvaluationEngine  # noqa: E402
from evaluation.industry_scoring import IndustryEvaluationEngine  # noqa: E402


class FixedRetriever:
    def __init__(self, items: list[RetrievedEvidence]):
        self.items = items

    async def search(self, _request):
        return self.items


def industry_evidence() -> list[RetrievedEvidence]:
    texts = [
        "企业已与两家制造客户签约，已有试点订单并完成首批交付。",
        "资料引用行业报告称，工业视觉市场保持高速增长，市场规模增长率为18%。",
        "企业在细分市场拥有12%的市场份额，并形成差异化技术壁垒。",
        "国家产业规划提供政策支持和专项资金，企业仍需单独核验适用条件。",
    ]
    return [
        RetrievedEvidence(
            task_id="industry-task",
            document_id="profile",
            document_name="industry_profile.pdf",
            page_number=index + 1,
            chunk_id=f"industry-{index}",
            text=text,
            locator=f"page-{index + 1}",
            score=0.9 - index * 0.1,
        )
        for index, text in enumerate(texts)
    ]


class IndustryAgentTest(unittest.TestCase):
    def test_industry_output_is_evidence_bound_and_complete(self) -> None:
        analysis = asyncio.run(
            IndustryAgent(FixedRetriever(industry_evidence())).analyze(
                IndustryAgentRequest(
                    task_id="industry-task",
                    enterprise_name="示例企业",
                )
            )
        )
        payload = analysis.model_dump(mode="json")
        self.assertEqual(set(payload), {"industry_indicators", "evidence"})
        self.assertEqual(
            set(payload["industry_indicators"]),
            {
                "market_potential",
                "industry_growth",
                "competitive_position",
                "policy_environment",
            },
        )
        evidence_ids = {item["evidence_id"] for item in payload["evidence"]}
        self.assertTrue(evidence_ids)
        for indicator in payload["industry_indicators"].values():
            self.assertIsNotNone(indicator["score"])
            self.assertTrue(indicator["evidence"])
            self.assertTrue(set(indicator["evidence"]) <= evidence_ids)

    def test_missing_industry_information_is_unscored(self) -> None:
        item = industry_evidence()[0].model_copy(update={"text": "企业正在研发新产品，计划未来拓展市场。"})
        analysis = asyncio.run(
            IndustryAgent(FixedRetriever([item])).analyze(
                IndustryAgentRequest(task_id="sparse-task")
            )
        )
        self.assertTrue(
            all(
                indicator.score is None
                for _, indicator in analysis.industry_indicators
            )
        )
        self.assertEqual(analysis.evidence, [])

    def test_industry_and_comprehensive_scores_are_deterministic(self) -> None:
        analysis = asyncio.run(
            IndustryAgent(FixedRetriever(industry_evidence())).analyze(
                IndustryAgentRequest(task_id="industry-task")
            )
        )
        industry_engine = IndustryEvaluationEngine.from_yaml(
            ROOT / "evaluation" / "industry" / "indicators.yaml",
            ROOT / "evaluation" / "industry" / "weights.yaml",
        )
        industry_result = industry_engine.evaluate(analysis)
        self.assertEqual(industry_result.industry_score, 73.75)
        technology_payload = json.loads(
            (ROOT / "data/examples/evaluation_result.json").read_text(encoding="utf-8")
        )
        technology_result = EvaluationResultInput.model_validate(technology_payload)
        comprehensive = ComprehensiveEvaluationEngine.from_yaml(
            ROOT / "evaluation" / "composite_weights.yaml"
        ).evaluate(technology_result, industry_result)
        self.assertEqual(comprehensive.overall_score, 73.15)
        self.assertEqual(comprehensive.dimension_scores, {"technology": 72.75, "industry": 73.75})

    def test_comprehensive_report_contains_industry_section(self) -> None:
        analysis_payload = json.loads(
            (ROOT / "data/examples/technology_analysis.json").read_text(encoding="utf-8")
        )
        technology_payload = json.loads(
            (ROOT / "data/examples/evaluation_result.json").read_text(encoding="utf-8")
        )
        technology_analysis = __import__(
            "app.agents.technology_agent", fromlist=["TechnologyAnalysis"]
        ).TechnologyAnalysis.model_validate(analysis_payload)
        industry_analysis = asyncio.run(
            IndustryAgent(FixedRetriever(industry_evidence())).analyze(
                IndustryAgentRequest(task_id="industry-task", evidence_id_start=4)
            )
        )
        industry_result = IndustryEvaluationEngine.from_yaml(
            ROOT / "evaluation/industry/indicators.yaml",
            ROOT / "evaluation/industry/weights.yaml",
        ).evaluate(industry_analysis)
        comprehensive = ComprehensiveEvaluationEngine.from_yaml(
            ROOT / "evaluation/composite_weights.yaml"
        ).evaluate(
            EvaluationResultInput.model_validate(technology_payload), industry_result
        )
        report = ComprehensiveReportGenerator().generate(
            ComprehensiveReportRequest(
                enterprise_name="示例企业",
                technology_analysis=technology_analysis,
                industry_analysis=industry_analysis,
                technology_evaluation=EvaluationResultInput.model_validate(technology_payload),
                industry_evaluation=industry_result.to_dict(),
                comprehensive_evaluation=comprehensive.to_dict(),
            )
        )
        self.assertEqual(report.overall_score, 73.15)
        self.assertEqual(report.industry_analysis.score, 73.75)
        self.assertTrue(report.industry_analysis.strengths)
        self.assertTrue(report.industry_analysis.risks == [])
        self.assertTrue(any(item.evidence_id == "E4" for item in report.references))


if __name__ == "__main__":
    unittest.main()
