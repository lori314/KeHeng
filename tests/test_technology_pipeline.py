"""End-to-end test for the local v0.3 indicator extraction loop."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.technology_agent import (  # noqa: E402
    TechnologyAgent,
    TechnologyAgentRequest,
)
from app.rag.document_parser import DocumentSource, PdfDocumentParser  # noqa: E402
from app.rag.embedding import LocalHashingEmbeddingProvider  # noqa: E402
from app.rag.knowledge_base import ChromaKnowledgeBase  # noqa: E402
from app.rag.retrieval import (  # noqa: E402
    KnowledgeBaseRetriever,
    RetrievalQuery,
)


class TechnologyPipelineTest(unittest.TestCase):
    def test_pdf_to_evidence_bound_indicators(self) -> None:
        asyncio.run(self._run_pipeline_assertions())

    async def _run_pipeline_assertions(self) -> None:
        pdf_path = ROOT / "data" / "examples" / "public_test_company_technology_profile.pdf"
        self.assertTrue(pdf_path.is_file())

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            embedder = LocalHashingEmbeddingProvider(dimensions=512)
            knowledge_base = ChromaKnowledgeBase(temp_dir, embedder)
            parser = PdfDocumentParser(chunk_size=420, chunk_overlap=60)
            source = DocumentSource(
                task_id="task-a",
                document_id="tech-doc-v1",
                file_name=pdf_path.name,
                content_type="application/pdf",
                local_path=str(pdf_path),
            )

            chunks = await knowledge_base.add_document(source, parser)
            self.assertGreaterEqual(len(chunks), 3)
            self.assertTrue(all(chunk.document_name == pdf_path.name for chunk in chunks))
            self.assertTrue(all(chunk.page_number for chunk in chunks))

            isolated = await knowledge_base.query(
                RetrievalQuery(task_id="task-b", query="核心技术", top_k=3)
            )
            self.assertEqual(isolated, [])

            agent = TechnologyAgent(KnowledgeBaseRetriever(knowledge_base))
            analysis = await agent.analyze(TechnologyAgentRequest(task_id="task-a"))
            payload = analysis.model_dump(mode="json")
            self.assertEqual(
                set(payload),
                {
                    "technology_indicators",
                    "technology_summary",
                    "summary_status",
                    "strengths",
                    "risks",
                    "evidence",
                },
            )
            self.assertNotIn("innovation_score", payload)
            self.assertEqual(
                set(payload["technology_indicators"]),
                {
                    "technical_autonomy",
                    "innovation_capability",
                    "intellectual_property",
                    "technical_maturity",
                },
            )
            for indicator in payload["technology_indicators"].values():
                self.assertIsNotNone(indicator["score"])
                self.assertTrue(indicator["evidence"])
            maturity = payload["technology_indicators"]["technical_maturity"]
            self.assertEqual(maturity["score"], 65)
            self.assertIn("TRL 6", maturity["rationale"])

            evidence_by_id = {
                evidence["evidence_id"]: evidence for evidence in payload["evidence"]
            }
            for indicator in payload["technology_indicators"].values():
                for evidence_id in indicator["evidence"]:
                    self.assertIn(evidence_id, evidence_by_id)
                    self.assertTrue(evidence_by_id[evidence_id]["document_name"])
                    self.assertIsNotNone(evidence_by_id[evidence_id]["page_number"])

            supported = {
                support
                for evidence in payload["evidence"]
                for support in evidence["supports"]
            }
            for indicator_id in payload["technology_indicators"]:
                self.assertIn(f"technology_indicators.{indicator_id}", supported)


if __name__ == "__main__":
    unittest.main()
