"""Regression tests for the fixed v0.5.5 competition evaluation cases."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from evaluation.evaluate_pipeline import evaluate_cases, load_cases


ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = ROOT / "data" / "evaluation_cases"


class EvaluationCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.first_runtime = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.second_runtime = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.first = asyncio.run(
            evaluate_cases(CASE_ROOT, cls.first_runtime.name)
        )
        cls.second = asyncio.run(
            evaluate_cases(CASE_ROOT, cls.second_runtime.name)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.first_runtime.cleanup()
        cls.second_runtime.cleanup()

    def test_three_cases_have_complete_fixture_structure_and_run(self) -> None:
        cases = load_cases(CASE_ROOT)
        self.assertEqual([case.case_id for case in cases], ["case_001", "case_002", "case_003"])
        self.assertEqual(self.first["case_count"], 3)
        self.assertEqual(self.first["passed_case_count"], 3)
        self.assertTrue(all(item["report_generated"] for item in self.first["cases"]))

    def test_case_evidence_is_isolated_and_citations_are_complete(self) -> None:
        self.assertEqual(self.first["metrics"]["task_id_isolation_accuracy"], 100.0)
        self.assertEqual(
            self.first["metrics"]["evidence_reference_completeness_rate"], 100.0
        )
        self.assertTrue(all(item["task_isolated"] for item in self.first["cases"]))

    def test_scores_are_stable_across_independent_runs(self) -> None:
        first_scores = {
            item["case_id"]: (item["technology_score"], item["dimension_scores"])
            for item in self.first["cases"]
        }
        second_scores = {
            item["case_id"]: (item["technology_score"], item["dimension_scores"])
            for item in self.second["cases"]
        }
        self.assertEqual(first_scores, second_scores)
        self.assertEqual(first_scores["case_001"][0], 82.75)
        self.assertIsNone(first_scores["case_002"][0])
        self.assertEqual(first_scores["case_003"][0], 41.36)

    def test_insufficient_and_negative_cases_do_not_over_infer(self) -> None:
        results = {item["case_id"]: item for item in self.first["cases"]}
        self.assertTrue(
            all(score is None for score in results["case_002"]["indicator_scores"].values())
        )
        self.assertEqual(
            results["case_003"]["indicator_scores"]["technical_maturity"], 25
        )
        self.assertEqual(results["case_003"]["errors"], [])

    def test_quality_metrics_are_complete(self) -> None:
        required = {
            "report_generation_success_rate",
            "evidence_reference_completeness_rate",
            "task_id_isolation_accuracy",
            "critical_field_completeness_rate",
        }
        self.assertTrue(required.issubset(self.first["metrics"]))
        for metric in required:
            self.assertEqual(self.first["metrics"][metric], 100.0)


if __name__ == "__main__":
    unittest.main()
