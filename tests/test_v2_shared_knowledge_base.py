"""Persistence and provenance tests for the shared V2 knowledge base."""

import sys
import tempfile
import time
import unittest
import hashlib
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
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceType,
)
from app.knowledge.identity import (  # noqa: E402
    citation_id_for,
    canonicalize_url,
    derived_fact_chunk_id_for,
    knowledge_chunk_id_for,
    source_id_for,
    source_version_for,
)
from app.knowledge.shared_knowledge_base import (  # noqa: E402
    KnowledgeSearchRequest,
    SharedKnowledgeBase,
)
from app.rag.document_parser import (  # noqa: E402
    DocumentChunk,
    DocumentSource,
    ParsedDocument,
    ParsedPage,
)
from app.rag.embedding import LocalHashingEmbeddingProvider  # noqa: E402


def web_snapshot(url: str, content: str, layer: KnowledgeLayer):
    canonical_url = canonicalize_url(url)
    source = Source(
        source_id=source_id_for(SourceType.COMPANY_OFFICIAL, canonical_url=canonical_url),
        source_type=SourceType.COMPANY_OFFICIAL,
        title="企业官网技术资料",
        canonical_url=canonical_url,
        publisher="企业官网",
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    retrieved = datetime(2026, 8, 1, tzinfo=timezone.utc)
    version = source_version_for(source, digest, retrieved_at=retrieved)
    locator = CitationLocator(
        section="技术能力", heading="核心材料", paragraph_number=1, url=canonical_url
    )
    citation = Citation(
        citation_id=citation_id_for(version.source_version_id, locator, content),
        source_id=source.source_id,
        source_version_id=version.source_version_id,
        source_url=canonical_url,
        source_title=source.title,
        excerpt=content,
        locator=locator,
    )
    chunk = KnowledgeChunk(
        chunk_id=knowledge_chunk_id_for(version.source_version_id, locator, content),
        text=content,
        source_id=source.source_id,
        source_version_id=version.source_version_id,
        citation=citation,
        company_id="co-example",
        knowledge_layer=layer,
    )
    return source, version, [chunk]


class SharedKnowledgeBaseTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.embedder = LocalHashingEmbeddingProvider(dimensions=256)
        self.kb = SharedKnowledgeBase(self.root, embedding_provider=self.embedder)

    async def asyncTearDown(self):
        self.kb.close()
        for attempt in range(5):
            try:
                self.tempdir.cleanup()
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)

    async def test_pdf_adapter_ids_are_stable_across_tasks_and_documents(self):
        digest = "b" * 64

        def adapted(task_id, document_id, legacy_chunk_id):
            parsed = ParsedDocument(
                source=DocumentSource(
                    task_id=task_id,
                    document_id=document_id,
                    file_name="材料.pdf",
                    content_type="application/pdf",
                    local_path="private/local/path.pdf",
                ),
                document_name="材料.pdf",
                pages=[ParsedPage(page_number=4, text="原文段落")],
                metadata={"source_sha256": digest},
            )
            source = parsed_document_to_source(parsed)
            chunk = DocumentChunk(
                task_id=task_id,
                document_id=document_id,
                document_name="材料.pdf",
                page_number=4,
                chunk_id=legacy_chunk_id,
                text="原文段落",
                locator="第 4 页 / 片段 1",
                metadata={"source_sha256": digest},
            )
            return source, document_chunk_to_knowledge_chunk(chunk, source)

        source_a, chunk_a = adapted("task-a", "doc-a", "legacy-a")
        source_b, chunk_b = adapted("task-b", "doc-b", "legacy-b")
        self.assertEqual(source_a.source_id, source_b.source_id)
        self.assertEqual(chunk_a.source_version_id, chunk_b.source_version_id)
        self.assertEqual(chunk_a.chunk_id, chunk_b.chunk_id)
        self.assertNotEqual(chunk_a.metadata["legacy_chunk_id"], chunk_b.metadata["legacy_chunk_id"])
        self.assertEqual(chunk_a.citation.locator.page_number, 4)

    async def test_same_url_and_content_is_idempotent_across_reimports(self):
        first = web_snapshot(
            "HTTPS://Example.COM:443/research/", "官网公开技术证据甲", KnowledgeLayer.ENTERPRISE
        )
        result1 = await self.kb.upsert_source_version(*first)
        second_source, second_version, second_chunks = web_snapshot(
            "https://example.com/research", "官网公开技术证据甲", KnowledgeLayer.ENTERPRISE
        )
        second_version = second_version.model_copy(
            update={"retrieved_at": datetime(2026, 8, 2, tzinfo=timezone.utc)}
        )
        result2 = await self.kb.upsert_source_version(
            second_source, second_version, second_chunks
        )
        self.assertTrue(result1.created_new_version)
        self.assertFalse(result2.created_new_version)
        self.assertEqual(result1.source.source_id, result2.source.source_id)
        self.assertEqual(
            result1.source_version.source_version_id,
            result2.source_version.source_version_id,
        )
        self.assertEqual(self.kb.repository.counts(first[0].source_id), {
            "sources": 1, "source_versions": 1, "current_versions": 1, "knowledge_chunks": 1
        })
        self.assertEqual(self.kb._collection.count(), 1)

    async def test_duplicate_chunk_ids_are_rejected_before_repository_or_chroma(self):
        source, version, chunks = web_snapshot(
            "https://example.com/duplicate-batch",
            "一条合成来源证据",
            KnowledgeLayer.GENERAL,
        )
        with self.assertRaisesRegex(ValueError, "Duplicate KnowledgeChunk IDs"):
            await self.kb.upsert_source_version(source, version, [chunks[0], chunks[0]])
        with self.assertRaisesRegex(ValueError, "Duplicate KnowledgeChunk IDs"):
            self.kb.repository.upsert_source_version(
                source, version, [chunks[0], chunks[0]]
            )
        self.assertEqual(self.kb.repository.counts(source.source_id)["knowledge_chunks"], 0)
        self.assertEqual(self.kb._collection.count(), 0)

    async def test_derived_fact_chunk_identity_is_stable_and_fact_specific(self):
        self.assertEqual(
            derived_fact_chunk_id_for("sv_test", "tf_same"),
            derived_fact_chunk_id_for("sv_test", "tf_same"),
        )
        self.assertNotEqual(
            derived_fact_chunk_id_for("sv_test", "tf_product_launch"),
            derived_fact_chunk_id_for("sv_test", "tf_customer_validation"),
        )
        self.assertEqual(
            derived_fact_chunk_id_for("sv_test", "tf_same").split("_", 1)[0],
            "kch",
        )

    async def test_new_content_preserves_history_but_search_returns_current_only(self):
        old = web_snapshot(
            "https://example.com/research", "官网公开技术证据旧版本", KnowledgeLayer.ENTERPRISE
        )
        new = web_snapshot(
            "https://example.com/research", "官网公开技术证据新版本", KnowledgeLayer.ENTERPRISE
        )
        old_result = await self.kb.upsert_source_version(*old)
        new_result = await self.kb.upsert_source_version(*new)
        self.assertTrue(new_result.created_new_version)
        self.assertEqual(
            new_result.previous_current_version_id,
            old_result.source_version.source_version_id,
        )
        versions = self.kb.repository.list_source_versions(old[0].source_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(sum(version.is_current for version in versions), 1)
        self.assertFalse(
            self.kb.repository.get_source_version(old_result.source_version.source_version_id).is_current
        )
        results = await self.kb.search(KnowledgeSearchRequest(query="公开技术证据", top_k=10))
        self.assertEqual([item.chunk.text for item in results], [new[2][0].text])

    async def test_late_older_capture_is_saved_without_replacing_current(self):
        source_current, version_current, chunks_current = web_snapshot(
            "https://example.com/late", "12点抓取的网页正文新内容", KnowledgeLayer.GENERAL
        )
        version_current = version_current.model_copy(
            update={"retrieved_at": datetime(2026, 9, 1, 12, tzinfo=timezone.utc)}
        )
        current_result = await self.kb.upsert_source_version(
            source_current, version_current, chunks_current
        )

        source_old, version_old, chunks_old = web_snapshot(
            "https://example.com/late", "11点抓取的网页正文旧内容", KnowledgeLayer.GENERAL
        )
        source_old = source_old.model_copy(update={"title": "更早抓取的页面标题"})
        version_old = version_old.model_copy(
            update={"retrieved_at": datetime(2026, 9, 1, 11, tzinfo=timezone.utc)}
        )
        old_result = await self.kb.upsert_source_version(
            source_old, version_old, chunks_old
        )

        self.assertFalse(old_result.became_current)
        self.assertEqual(old_result.previous_current_version_id, current_result.source_version.source_version_id)
        self.assertEqual(
            self.kb.repository.get_source(source_current.source_id).title,
            source_current.title,
        )
        self.assertFalse(
            self.kb.repository.get_source_version(version_old.source_version_id).is_current
        )
        self.assertTrue(
            self.kb.repository.get_source_version(version_current.source_version_id).is_current
        )
        results = await self.kb.search(KnowledgeSearchRequest(query="网页正文内容", top_k=10))
        self.assertEqual([item.chunk.text for item in results], [chunks_current[0].text])

    async def test_filters_and_search_preserve_full_citation(self):
        enterprise = web_snapshot(
            "https://example.com/technology", "企业技术事实及来源原文", KnowledgeLayer.ENTERPRISE
        )
        await self.kb.upsert_source_version(*enterprise)
        # A single logical URL can only have one source version; use a second source URL
        # for a separately filterable layer.
        standard = web_snapshot(
            "https://example.com/standard", "企业适用标准和来源原文", KnowledgeLayer.STANDARD
        )
        await self.kb.upsert_source_version(*standard)
        financial = web_snapshot(
            "https://example.com/financial-rule",
            "企业金融规则及来源原文",
            KnowledgeLayer.FINANCIAL_RULE,
        )
        await self.kb.upsert_source_version(*financial)
        filtered = await self.kb.search(
            KnowledgeSearchRequest(
                query="金融规则来源原文",
                top_k=10,
                company_id="co-example",
                knowledge_layers=[KnowledgeLayer.FINANCIAL_RULE],
            )
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].chunk.knowledge_layer, KnowledgeLayer.FINANCIAL_RULE)
        citation = filtered[0].chunk.citation
        self.assertEqual(citation.source_id, filtered[0].source.source_id)
        self.assertEqual(citation.source_version_id, filtered[0].source_version.source_version_id)
        self.assertEqual(citation.source_url, "https://example.com/financial-rule")
        self.assertEqual(citation.source_title, "企业官网技术资料")
        self.assertEqual(citation.excerpt, filtered[0].chunk.text)
        self.assertEqual(citation.locator.heading, "核心材料")

    async def test_sqlite_and_chroma_survive_reopen(self):
        snapshot = web_snapshot(
            "https://example.com/persist", "可持久化来源及完整证据文本", KnowledgeLayer.GENERAL
        )
        await self.kb.upsert_source_version(*snapshot)
        source_id = snapshot[0].source_id
        chunk_id = snapshot[2][0].chunk_id
        version_id = snapshot[1].source_version_id
        self.kb.close()
        self.kb = SharedKnowledgeBase(self.root, embedding_provider=self.embedder)
        self.assertIsNotNone(self.kb.repository.get_source(source_id))
        self.assertIsNotNone(self.kb.repository.get_source_version(version_id))
        self.assertEqual(self.kb.repository.get_chunk(chunk_id).text, snapshot[2][0].text)
        found = await self.kb.search(KnowledgeSearchRequest(query="完整证据文本", top_k=5))
        self.assertEqual([item.chunk.chunk_id for item in found], [chunk_id])


if __name__ == "__main__":
    unittest.main()
