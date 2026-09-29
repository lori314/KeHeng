"""Deterministic score, weight, evidence and missing-value tests."""

import unittest
from copy import deepcopy
from pathlib import Path

from evaluation.scoring import TechnologyEvaluationEngine

ROOT = Path(__file__).resolve().parents[1]


def fixed_analysis() -> dict:
    return {
        "technology_indicators": {
            "technical_autonomy": {
                "score": 80,
                "evidence": ["E1"],
                "rationale": "固定测试输入",
            },
            "innovation_capability": {
                "score": 70,
                "evidence": ["E1", "E2"],
                "rationale": "固定测试输入",
            },
            "intellectual_property": {
                "score": 50,
                "evidence": ["E2"],
                "rationale": "固定测试输入",
            },
            "technical_maturity": {
                "score": 65,
                "evidence": ["E3"],
                "rationale": "固定测试输入",
            },
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "document_name": "fixed.pdf",
                "page_number": 1,
                "chunk_id": "chunk-1",
                "excerpt": "自主研发技术方案",
            },
            {
                "evidence_id": "E2",
                "document_name": "fixed.pdf",
                "page_number": 2,
                "chunk_id": "chunk-2",
                "excerpt": "专利与创新活动",
            },
            {
                "evidence_id": "E3",
                "document_name": "fixed.pdf",
                "page_number": 3,
                "chunk_id": "chunk-3",
                "excerpt": "试点验证",
            },
        ],
    }


class TechnologyEvaluationEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = TechnologyEvaluationEngine.from_yaml(
            ROOT / "evaluation" / "indicators.yaml",
            ROOT / "evaluation" / "weights.yaml",
        )

    def test_weighted_score_and_dimensions_are_deterministic(self) -> None:
        result = self.engine.evaluate(fixed_analysis()).to_dict()
        self.assertEqual(
            set(result),
            {
                "technology_score",
                "dimension_scores",
                "score_explanation",
                "evidence_mapping",
            },
        )
        self.assertAlmostEqual(sum(self.engine.weight_set.weights.values()), 1.0)
        self.assertEqual(result["technology_score"], 67.25)
        self.assertEqual(
            result["dimension_scores"],
            {"innovation": 74.55, "ip": 50.0, "maturity": 65.0},
        )
        scored = [
            item for item in result["score_explanation"] if item["status"] == "scored"
        ]
        self.assertEqual(len(scored), 4)
        self.assertEqual(scored[0]["calculation"], "80 × 25.00% = 20.00")

    def test_evidence_sources_are_preserved(self) -> None:
        result = self.engine.evaluate(fixed_analysis()).to_dict()
        autonomy = next(
            item
            for item in result["evidence_mapping"]
            if item["indicator_id"] == "technical_autonomy"
        )
        self.assertEqual(autonomy["evidence_ids"], ["E1"])
        self.assertEqual(autonomy["sources"][0]["document_name"], "fixed.pdf")
        self.assertEqual(autonomy["sources"][0]["page_number"], 1)

    def test_missing_indicator_is_unscored_and_does_not_crash(self) -> None:
        payload = deepcopy(fixed_analysis())
        del payload["technology_indicators"]["intellectual_property"]
        result = self.engine.evaluate(payload).to_dict()
        self.assertEqual(result["technology_score"], 71.56)
        self.assertIsNone(result["dimension_scores"]["ip"])
        missing = next(
            item
            for item in result["score_explanation"]
            if item.get("indicator_id") == "intellectual_property"
        )
        self.assertEqual(missing["status"], "unscored")
        self.assertTrue(
            any(item["status"] == "partial_score" for item in result["score_explanation"])
        )

    def test_score_without_valid_evidence_is_not_counted(self) -> None:
        payload = deepcopy(fixed_analysis())
        payload["technology_indicators"]["intellectual_property"]["evidence"] = [
            "UNKNOWN"
        ]
        result = self.engine.evaluate(payload).to_dict()
        self.assertIsNone(result["dimension_scores"]["ip"])

    def test_all_missing_indicators_return_unscored(self) -> None:
        result = self.engine.evaluate(
            {"technology_indicators": {}, "evidence": []}
        ).to_dict()
        self.assertIsNone(result["technology_score"])
        self.assertEqual(
            result["dimension_scores"],
            {"innovation": None, "ip": None, "maturity": None},
        )


if __name__ == "__main__":
    unittest.main()
