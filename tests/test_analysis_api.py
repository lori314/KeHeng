"""Upload, task isolation, failure handling and unified workflow tests."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import create_app  # noqa: E402
from app.services.analysis_service import (  # noqa: E402
    TechnologyAnalysisInput,
    TechnologyAssessmentService,
)
from app.services.analysis_tasks import (  # noqa: E402
    AnalysisTaskManager,
    get_analysis_task_manager,
)


SAMPLE_PDF = ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"


class AnalysisApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.runtime_root = Path(self._temporary_directory.name)
        self.service = TechnologyAssessmentService(
            project_root=ROOT,
            runtime_root=self.runtime_root / "knowledge_bases",
        )
        self.manager = AnalysisTaskManager(
            service=self.service,
            upload_root=self.runtime_root / "uploads",
        )
        application = create_app()
        application.dependency_overrides[get_analysis_task_manager] = (
            lambda: self.manager
        )
        self.client = TestClient(application)

    def tearDown(self) -> None:
        self.client.close()
        self._temporary_directory.cleanup()

    def _upload(
        self,
        *,
        file_name: str = "company.pdf",
        content: bytes | None = None,
        content_type: str = "application/pdf",
        enterprise_name: str = "测试科技企业",
    ):
        return self.client.post(
            "/analysis/create",
            data={"enterprise_name": enterprise_name},
            files={
                "file": (
                    file_name,
                    SAMPLE_PDF.read_bytes() if content is None else content,
                    content_type,
                )
            },
        )

    def test_local_replay_control_availability_matches_saved_file_presence(self) -> None:
        replay_file = self.runtime_root / "saved" / "paired_model_results.json"
        with patch("app.api.routes.analysis._ITERATION04_RESULT", replay_file):
            self.assertEqual(
                self.client.get("/analysis/replays/iteration04-nio-hash/availability").json(),
                {"available": False},
            )
            replay_file.parent.mkdir()
            replay_file.write_text("{}", encoding="utf-8")
            self.assertEqual(
                self.client.get("/analysis/replays/iteration04-nio-hash/availability").json(),
                {"available": True},
            )

    def test_pdf_upload_completes_and_returns_dynamic_report(self) -> None:
        created = self._upload(enterprise_name="动态测试企业")
        self.assertEqual(created.status_code, 202)
        task = created.json()
        self.assertEqual(task["status"], "processing")
        self.assertTrue(task["task_id"])

        response = self.client.get(f"/analysis/{task['task_id']}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["report"]["enterprise_name"], "动态测试企业")
        self.assertEqual(payload["report"]["task_id"], task["task_id"])
        self.assertEqual(payload["report"]["technology_score"], 72.75)
        self.assertTrue(payload["report"]["references"])
        self.assertEqual(payload["request_mode"], "rule_demo")
        self.assertEqual(payload["run_info"]["actual_mode"], "rule_demo")
        self.assertEqual(payload["modules"]["industry"]["status"], "not_run")

    def test_tasks_use_distinct_knowledge_bases_and_evidence(self) -> None:
        first = self._upload(file_name="alpha.pdf", enterprise_name="甲企业").json()
        second = self._upload(file_name="beta.pdf", enterprise_name="乙企业").json()
        self.assertNotEqual(first["task_id"], second["task_id"])

        first_result = self.client.get(f"/analysis/{first['task_id']}").json()
        second_result = self.client.get(f"/analysis/{second['task_id']}").json()
        first_documents = {
            item["document_name"] for item in first_result["report"]["references"]
        }
        second_documents = {
            item["document_name"] for item in second_result["report"]["references"]
        }
        self.assertEqual(first_documents, {"alpha.pdf"})
        self.assertEqual(second_documents, {"beta.pdf"})
        self.assertTrue(
            (self.runtime_root / "knowledge_bases" / first["task_id"] / "chroma").is_dir()
        )
        self.assertTrue(
            (self.runtime_root / "knowledge_bases" / second["task_id"] / "chroma").is_dir()
        )

    def test_unified_service_returns_all_analysis_artifacts(self) -> None:
        artifacts = asyncio.run(
            self.service.run_with_artifacts(
                TechnologyAnalysisInput(
                    task_id="direct-service-test",
                    enterprise_name="统一服务测试企业",
                    pdf_path=SAMPLE_PDF,
                    original_file_name=SAMPLE_PDF.name,
                )
            )
        )
        self.assertTrue(artifacts.technology_analysis.evidence)
        self.assertEqual(artifacts.evaluation_result.technology_score, 72.75)
        self.assertEqual(artifacts.report.technology_score, 72.75)
        self.assertEqual(artifacts.report.enterprise_name, "统一服务测试企业")

    def test_non_pdf_and_empty_uploads_are_rejected_immediately(self) -> None:
        non_pdf = self._upload(
            file_name="notes.txt", content=b"plain text", content_type="text/plain"
        )
        self.assertEqual(non_pdf.status_code, 415)
        self.assertEqual(non_pdf.json()["detail"]["code"], "unsupported_file_type")

        empty = self._upload(content=b"")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"]["code"], "empty_file")

    def test_invalid_pdf_signature_is_rejected_immediately(self) -> None:
        response = self._upload(content=b"not really a pdf")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["detail"]["code"], "invalid_pdf_signature")

    def test_damaged_pdf_becomes_failed_task_with_clear_error(self) -> None:
        created = self._upload(content=b"%PDF-1.7\ninvalid structure").json()
        result = self.client.get(f"/analysis/{created['task_id']}").json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "damaged_pdf")
        self.assertIsNone(result["report"])

    def test_pdf_without_text_becomes_failed_task_with_clear_error(self) -> None:
        document = pymupdf.open()
        document.new_page()
        no_text_pdf = document.tobytes()
        document.close()

        created = self._upload(content=no_text_pdf).json()
        result = self.client.get(f"/analysis/{created['task_id']}").json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "no_extractable_text")
        self.assertIn("OCR", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
