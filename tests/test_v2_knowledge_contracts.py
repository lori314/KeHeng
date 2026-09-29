"""Regression tests for V2 knowledge contracts and the legacy PDF adapter."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.adapters import (  # noqa: E402
    document_chunk_to_knowledge_chunk,
    parsed_document_to_source,
)
from app.knowledge.contracts import (  # noqa: E402
    Citation,
    CitationLocator,
    Company,
    CompanyResolutionStatus,
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceType,
)
from app.rag.document_parser import (  # noqa: E402
    DocumentChunk,
    DocumentSource,
    ParsedDocument,
    ParsedPage,
)


class V2KnowledgeContractsTest(unittest.TestCase):
    def test_web_source_citation_and_chunk_round_trip_preserve_provenance(self) -> None:
        source = Source(
            source_id="src-company-official-001",
            source_type=SourceType.COMPANY_OFFICIAL,
            title="某科技公司技术介绍",
            canonical_url="https://example.com/technology",
            publisher="某科技公司",
            published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        citation = Citation(
            citation_id="cit-company-site-p3",
            source_id=source.source_id,
            source_url=source.canonical_url,
            source_title=source.title,
            excerpt="公司公开介绍中的技术描述。",
            locator=CitationLocator(
                section="技术能力",
                heading="核心技术",
                paragraph_number=2,
                text_anchor="core-technology",
                url=source.canonical_url,
            ),
        )
        chunk = KnowledgeChunk(
            chunk_id="chunk-company-site-p3",
            text=citation.excerpt,
            source_id=source.source_id,
            citation=citation,
            company_id="company-001",
            knowledge_layer=KnowledgeLayer.ENTERPRISE,
        )

        restored = KnowledgeChunk.model_validate(chunk.model_dump(mode="json"))
        self.assertEqual(restored.source_id, source.source_id)
        self.assertEqual(restored.citation.source_url, "https://example.com/technology")
        self.assertEqual(restored.citation.source_title, "某科技公司技术介绍")
        self.assertEqual(restored.citation.excerpt, "公司公开介绍中的技术描述。")
        self.assertEqual(restored.citation.locator.heading, "核心技术")

    def test_legacy_pdf_chunk_maps_without_losing_page_text_or_hash(self) -> None:
        source_hash = "a" * 64
        parsed = ParsedDocument(
            source=DocumentSource(
                task_id="task-local-01",
                document_id="document-01",
                file_name="profile.pdf",
                content_type="application/pdf",
                local_path="local-only-placeholder.pdf",
            ),
            document_name="profile.pdf",
            pages=[ParsedPage(page_number=7, text="第七页上的原始技术证据。")],
            metadata={"source_sha256": source_hash, "parser_version": "pymupdf-v1"},
        )
        source = parsed_document_to_source(parsed)
        other_task_parsed = parsed.model_copy(
            update={
                "source": parsed.source.model_copy(
                    update={"task_id": "task-local-02"}
                )
            }
        )
        self.assertEqual(
            parsed_document_to_source(other_task_parsed).source_id,
            source.source_id,
        )
        chunk = DocumentChunk(
            task_id="task-local-01",
            document_id="document-01",
            document_name="profile.pdf",
            page_number=7,
            chunk_id="legacy-task-chunk-id",
            text="第七页上的原始技术证据。",
            locator="第 7 页 / 片段 1",
            metadata={"source_sha256": source_hash, "parser_version": "pymupdf-v1"},
        )

        mapped = document_chunk_to_knowledge_chunk(chunk, source)
        self.assertNotEqual(mapped.chunk_id, chunk.chunk_id)
        self.assertEqual(mapped.metadata["legacy_chunk_id"], chunk.chunk_id)
        self.assertIsNotNone(mapped.source_version_id)
        self.assertEqual(mapped.citation.source_version_id, mapped.source_version_id)
        self.assertEqual(mapped.citation.locator.page_number, 7)
        self.assertEqual(mapped.citation.locator.locator_text, chunk.locator)
        self.assertEqual(mapped.text, chunk.text)
        self.assertEqual(mapped.citation.excerpt, chunk.text)
        self.assertEqual(mapped.metadata["source_sha256"], source_hash)
        self.assertEqual(mapped.metadata["task_id"], "task-local-01")
        self.assertNotIn("local_path", source.metadata)
        self.assertNotIn("task-local-01", source.source_id)

    def test_company_name_alone_is_valid_before_resolution(self) -> None:
        company = Company(canonical_name="某科技公司")
        self.assertIsNone(company.company_id)
        self.assertEqual(company.resolution_status, CompanyResolutionStatus.UNRESOLVED)
        self.assertEqual(company.aliases, [])
        self.assertIsNone(company.official_website)
        self.assertIsNone(company.unified_social_credit_code)

    def test_knowledge_layers_share_one_chunk_contract(self) -> None:
        source = Source(
            source_id="src-standard-001",
            source_type=SourceType.STANDARD,
            title="示例标准",
        )
        citation = Citation(
            citation_id="cit-standard-001",
            source_id=source.source_id,
            source_title=source.title,
            excerpt="标准条文摘录。",
        )
        chunks = [
            KnowledgeChunk(
                chunk_id=f"chunk-{layer.value}",
                text=citation.excerpt,
                source_id=source.source_id,
                citation=citation,
                knowledge_layer=layer,
            )
            for layer in (
                KnowledgeLayer.ENTERPRISE,
                KnowledgeLayer.STANDARD,
                KnowledgeLayer.FINANCIAL_RULE,
            )
        ]

        self.assertEqual(
            [chunk.knowledge_layer for chunk in chunks],
            [
                KnowledgeLayer.ENTERPRISE,
                KnowledgeLayer.STANDARD,
                KnowledgeLayer.FINANCIAL_RULE,
            ],
        )


if __name__ == "__main__":
    unittest.main()
