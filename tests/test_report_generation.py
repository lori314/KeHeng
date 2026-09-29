"""Structured report generator and FastAPI endpoint tests."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.technology_agent import TechnologyAnalysis  # noqa: E402
from app.main import app  # noqa: E402
from app.report import (  # noqa: E402
    EvaluationResultInput,
    ReportGenerator,
    ReportRequest,
)


def load_payloads() -> tuple[dict, dict]:
    analysis = json.loads(
        (ROOT / "data" / "examples" / "technology_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = json.loads(
        (ROOT / "data" / "examples" / "evaluation_result.json").read_text(
            encoding="utf-8"
        )
    )
    return analysis, evaluation


class ReportGeneratorTest(unittest.TestCase):
    def test_report_is_complete_and_preserves_score_and_evidence(self) -> None:
        analysis_payload, evaluation_payload = load_payloads()
        evaluation_payload["technology_score"] = 12.34
        evaluation_payload["dimension_scores"]["innovation"] = 9.87
        report = ReportGenerator().generate(
            ReportRequest(
                technology_analysis=TechnologyAnalysis.model_validate(
                    analysis_payload
                ),
                evaluation_result=EvaluationResultInput.model_validate(
                    evaluation_payload
                ),
            )
        )
        payload = report.model_dump(mode="json")
        self.assertEqual(
            set(payload),
            {
                "task_id",
                "enterprise_name",
                "title",
                "summary",
                "technology_score",
                "dimension_scores",
                "strengths",
                "risks",
                "evaluation_details",
                "references",
            },
        )
        self.assertEqual(payload["technology_score"], 12.34)
        self.assertEqual(payload["enterprise_name"], "未命名企业")
        self.assertEqual(
            payload["dimension_scores"], evaluation_payload["dimension_scores"]
        )
        self.assertEqual(payload["dimension_scores"]["innovation"], 9.87)
        reference_ids = {item["evidence_id"] for item in payload["references"]}
        self.assertEqual(reference_ids, {"E1", "E2", "E3"})
        for finding in payload["strengths"] + payload["risks"]:
            self.assertTrue(finding["evidence"])
            for evidence in finding["evidence"]:
                self.assertIn(evidence["evidence_id"], finding["content"])
                self.assertTrue(evidence["document_name"])
                self.assertIsNotNone(evidence["page_number"])
                self.assertTrue(evidence["excerpt"])

    def test_report_endpoint_returns_json(self) -> None:
        analysis_payload, evaluation_payload = load_payloads()
        response = TestClient(app).post(
            "/report/generate",
            json={
                "technology_analysis": analysis_payload,
                "evaluation_result": evaluation_payload,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["technology_score"], 72.75)
        self.assertEqual(len(payload["references"]), 3)

    def test_report_endpoint_rejects_broken_citation(self) -> None:
        analysis_payload, evaluation_payload = load_payloads()
        broken = deepcopy(analysis_payload)
        broken["strengths"][0] = "无法定位的优势[E99]"
        response = TestClient(app).post(
            "/report/generate",
            json={
                "technology_analysis": broken,
                "evaluation_result": evaluation_payload,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("E99", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
