"""Deterministic limits and diversity for semantic evidence intake."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.knowledge.contracts import Citation, CitationLocator, KnowledgeChunk, SourceType
from app.knowledge.semantic.contracts import SourceQuality
from app.knowledge.semantic.evidence_selector import select_semantic_evidence


def make_chunk(source_id: str, index: int, text: str, *, source_type: str = "web") -> KnowledgeChunk:
    url = f"https://{source_id.lower()}.example.test/page/{index}"
    citation = Citation(
        citation_id=f"citation-{source_id}-{index}",
        source_id=source_id,
        source_version_id=f"version-{source_id}",
        source_url=url,
        source_title=f"Source {source_id}",
        excerpt=text,
        locator=CitationLocator(url=url, paragraph_number=index + 1, text_anchor=f"p-{index + 1}"),
    )
    return KnowledgeChunk(
        chunk_id=f"chunk-{source_id}-{index}",
        text=text,
        source_id=source_id,
        source_version_id=f"version-{source_id}",
        citation=citation,
        metadata={"content_scope": "full_content"},
    )


def quality(category: str, *, scope: str = "full_content", source_type: str = "web") -> SourceQuality:
    return SourceQuality(
        category=category,
        source_type=source_type,
        content_scope=scope,
        rationale="synthetic test fixture",
    )


class SemanticEvidenceSelectorTests(unittest.TestCase):
    def test_round_robin_caps_sources_and_preserves_diversity(self):
        chunks = []
        qualities = {}
        for source_id, count in (("A", 30), ("B", 3), ("C", 3), ("D", 2)):
            for index in range(count):
                chunk = make_chunk(source_id, index, f"{source_id} technology evidence {index} " + (f"distinct-{source_id}-{index} " * 20))
                chunks.append(chunk)
                qualities[chunk.chunk_id] = quality("weak_web")

        selected = select_semantic_evidence(chunks, qualities)
        selected_ids = [item.chunk.chunk_id for item in selected.items]
        source_counts = {}
        for item in selected.items:
            source_counts[item.chunk.source_id] = source_counts.get(item.chunk.source_id, 0) + 1

        self.assertGreaterEqual(selected.report.selected_source_count, 4)
        self.assertEqual(selected_ids[:4], ["chunk-A-0", "chunk-B-0", "chunk-C-0", "chunk-D-0"])
        self.assertTrue(all(count <= 2 for count in source_counts.values()))
        self.assertLessEqual(selected.report.selected_chunk_count, 18)

    def test_quality_and_full_content_rank_before_weak_and_snippet_sources(self):
        chunks = [
            make_chunk("Z", 0, "snippet evidence"),
            make_chunk("B", 0, "weak full text"),
            make_chunk("A", 0, "first party text"),
            make_chunk("C", 0, "weak search snippet"),
        ]
        qualities = {
            chunks[0].chunk_id: quality("snippet_only", scope="search_snippet"),
            chunks[1].chunk_id: quality("weak_web"),
            chunks[2].chunk_id: quality("first_party", source_type=SourceType.COMPANY_OFFICIAL.value),
            chunks[3].chunk_id: quality("weak_web", scope="search_snippet"),
        }

        selected = select_semantic_evidence(chunks, qualities, max_chunks=3)

        self.assertEqual([item.chunk.source_id for item in selected.items], ["A", "B", "C"])
        self.assertEqual(selected.report.quality_distribution, {"first_party": 1, "weak_web": 2})
        self.assertEqual(selected.report.content_scope_distribution, {"full_content": 2, "search_snippet": 1})

    def test_character_budget_is_hard_and_counts_truncated_semantic_text(self):
        chunks = [make_chunk(chr(65 + index), 0, chr(65 + index) * 4000) for index in range(4)]
        qualities = {chunk.chunk_id: quality("weak_web") for chunk in chunks}
        selected = select_semantic_evidence(
            chunks, qualities, max_input_chars=5000, max_chunks=4, max_chars_per_chunk=3200
        )

        model_chunks = selected.model_chunks
        self.assertLessEqual(selected.report.selected_char_count, 5000)
        self.assertEqual(selected.report.selected_char_count, sum(len(chunk.text) for chunk in model_chunks))
        self.assertEqual(selected.report.truncated_chunk_count, 1)
        self.assertTrue(all(len(chunk.text) <= 3200 for chunk in model_chunks))
        self.assertEqual(selected.report.dropped_due_to_budget, 3)
        self.assertEqual(chunks[0].text, "A" * 4000)
        self.assertNotEqual(model_chunks[0].text, chunks[0].text)

    def test_selection_order_and_report_are_stable(self):
        chunks = [
            make_chunk("B", 1, "second source evidence"),
            make_chunk("A", 1, "first source evidence"),
            make_chunk("A", 0, "first source opening"),
        ]
        qualities = {chunk.chunk_id: quality("third_party", source_type="news") for chunk in chunks}

        first = select_semantic_evidence(chunks, qualities)
        second = select_semantic_evidence(list(reversed(chunks)), qualities)

        self.assertEqual([item.chunk.chunk_id for item in first.items], [item.chunk.chunk_id for item in second.items])
        self.assertEqual(first.report, second.report)

    def test_near_duplicate_is_filtered_but_distinct_evidence_is_kept(self):
        shared = "The company disclosed its accelerator architecture and product development plans. "
        repeated = shared * 8
        chunks = [
            make_chunk("A", 0, repeated),
            make_chunk("B", 0, repeated[:-1] + "!"),
            make_chunk("C", 0, ("A separate report describes laboratory testing and prototype validation. " * 8)),
        ]
        qualities = {chunk.chunk_id: quality("third_party", source_type="news") for chunk in chunks}

        selected = select_semantic_evidence(chunks, qualities)

        self.assertEqual([item.chunk.source_id for item in selected.items], ["A", "C"])
        self.assertEqual(selected.report.dropped_as_duplicate, 1)


if __name__ == "__main__":
    unittest.main()
