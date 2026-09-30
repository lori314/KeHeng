"""Template-aware deterministic retrieval for technology fact evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError

from app.knowledge.contracts import Citation, CitationLocator, KnowledgeChunk
from app.knowledge.semantic.contracts import SourceQuality
from app.knowledge.semantic.evidence_selector import (
    select_semantic_evidence,
    select_template_fact_evidence,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry, TechnologyTemplate


def make_chunk(source_id: str, index: int, text: str) -> KnowledgeChunk:
    url = f"https://{source_id}.example.test/page/{index}"
    citation = Citation(
        citation_id=f"citation-{source_id}-{index}",
        source_id=source_id,
        source_version_id=f"version-{source_id}",
        source_url=url,
        source_title=f"Source {source_id}",
        excerpt=text,
        locator=CitationLocator(
            url=url,
            page_number=index + 1,
            paragraph_number=index + 1,
            text_anchor=f"p-{index + 1}",
        ),
    )
    return KnowledgeChunk(
        chunk_id=f"chunk-{source_id}-{index}",
        text=text,
        source_id=source_id,
        source_version_id=f"version-{source_id}",
        citation=citation,
        metadata={"content_scope": "full_content"},
    )


def quality(category: str) -> SourceQuality:
    return SourceQuality(
        category=category,
        source_type="web",
        content_scope="full_content",
        rationale="synthetic selector test",
    )


class TemplateFactEvidenceSelectorTests(unittest.TestCase):
    def setUp(self):
        self.registry = KnowledgeSemanticRegistry()

    def select(self, chunks, qualities, template_ids, *, classifier_chunks=None, **limits):
        return select_template_fact_evidence(
            chunks,
            qualities,
            self.registry,
            template_ids,
            classifier_chunks=classifier_chunks or [],
            **limits,
        )

    def test_late_technical_chunk_beats_opening_context_from_same_large_source(self):
        chunks = [
            make_chunk("annual", i, f"工商背景与注册地址信息第{i}段。" + (f"一般公司资料{i} " * 25))
            for i in range(12)
        ]
        target = make_chunk("annual", 12, "芯片完成流片并进行硅片验证，披露了回片后的芯片测试进展。")
        chunks.append(target)
        qualities = {item.chunk_id: quality("weak_web") for item in chunks}

        broad = select_semantic_evidence(chunks, qualities)
        fact = self.select(chunks, qualities, ["semiconductor_design"], classifier_chunks=broad.original_chunks)

        self.assertNotIn(target.chunk_id, {item.chunk.chunk_id for item in broad.items})
        self.assertEqual(fact.items[0].chunk.chunk_id, target.chunk_id)
        self.assertGreater(fact.items[0].template_match_score, 0)

    def test_technical_relevance_precedes_quality_but_quality_breaks_ties(self):
        unrelated = make_chunk("official", 0, "企业注册地址、股本和组织信息。")
        weak_technical = make_chunk("web", 0, "芯片完成流片并开展硅片验证，披露条目甲。")
        same_score_weak = make_chunk("weak", 0, "芯片完成流片并开展硅片验证，披露条目乙。")
        same_score_authoritative = make_chunk("authority", 0, "芯片完成流片并开展硅片验证，披露条目丙。")
        chunks = [unrelated, weak_technical, same_score_weak, same_score_authoritative]
        qualities = {
            unrelated.chunk_id: quality("authoritative_public_record"),
            weak_technical.chunk_id: quality("weak_web"),
            same_score_weak.chunk_id: quality("weak_web"),
            same_score_authoritative.chunk_id: quality("authoritative_public_record"),
        }

        selected = self.select(
            chunks,
            qualities,
            ["semiconductor_design"],
            classifier_chunks=[unrelated],
            max_chunks=4,
            min_context_chunks=4,
        )
        ids = [item.chunk.chunk_id for item in selected.items]

        self.assertLess(ids.index(weak_technical.chunk_id), ids.index(unrelated.chunk_id))
        self.assertLess(ids.index(same_score_authoritative.chunk_id), ids.index(same_score_weak.chunk_id))

    def test_source_cap_keeps_top_three_relevant_chunks_and_leaves_room_for_other_sources(self):
        annual = [
            make_chunk("annual", 0, "芯片一般情况。"),
            make_chunk("annual", 1, "芯片完成流片，相关集成电路流片进展已披露。"),
            make_chunk("annual", 2, "芯片架构设计完成，披露芯片架构和设计实现。"),
            make_chunk("annual", 3, "硅片验证和芯片测试结果已披露，完成流片后硅片验证。"),
        ]
        other = make_chunk("other", 0, "芯片设计与处理器技术规格已公开。")
        chunks = annual + [other]
        qualities = {item.chunk_id: quality("weak_web") for item in chunks}

        selected = self.select(
            chunks,
            qualities,
            ["semiconductor_design"],
            classifier_chunks=[],
            max_chunks=4,
            min_context_chunks=0,
        )
        ids = [item.chunk.chunk_id for item in selected.items]
        annual_ids = [chunk.chunk_id for chunk in annual]

        self.assertLessEqual(sum(item.chunk.source_id == "annual" for item in selected.items), 3)
        self.assertNotIn(annual_ids[0], ids)
        self.assertIn(other.chunk_id, ids)
        self.assertIn(annual_ids[1], ids)
        self.assertIn(annual_ids[2], ids)
        self.assertIn(annual_ids[3], ids)

    def test_selected_template_changes_which_technical_passage_ranks_first(self):
        chip = make_chunk("chip", 0, "芯片完成流片并进行硅片验证。")
        software = make_chunk("software", 0, "AI 软件版本发布，模型推理部署通过 benchmark 基准测试。")
        chunks = [software, chip]
        qualities = {item.chunk_id: quality("weak_web") for item in chunks}

        semiconductor = self.select(chunks, qualities, ["semiconductor_design"], min_context_chunks=0)
        software_ai = self.select(chunks, qualities, ["software_ai"], min_context_chunks=0)

        self.assertEqual(semiconductor.items[0].chunk.chunk_id, chip.chunk_id)
        self.assertEqual(software_ai.items[0].chunk.chunk_id, software.chunk_id)

    def test_zero_positive_matches_fall_back_to_all_available_classifier_chunks(self):
        chunks = [make_chunk(f"generic{i}", i, f"一般背景说明第{i}条，不包含模板技术术语。") for i in range(5)]
        qualities = {item.chunk_id: quality("weak_web") for item in chunks}
        classifier = select_semantic_evidence(chunks, qualities, max_chunks_per_source=5)

        selected = self.select(
            chunks,
            qualities,
            ["semiconductor_design"],
            classifier_chunks=classifier.original_chunks,
        )

        self.assertEqual(selected.report.positive_match_candidate_count, 0)
        self.assertEqual(selected.report.fallback_fill_count, 5)
        self.assertEqual([item.chunk.chunk_id for item in selected.items], [item.chunk_id for item in chunks])

    def test_all_template_selection_terms_are_present_and_normalized_unique(self):
        registry = self.registry
        self.assertEqual(registry.templates.registry_version, "technology-templates.v2")
        for template in registry.templates.templates:
            normalized = [" ".join(term.casefold().split()) for term in template.selection_terms]
            self.assertTrue(all(normalized))
            self.assertEqual(len(normalized), len(set(normalized)), template.id)

        base = {
            "id": "fixture",
            "name": "fixture",
            "important_objects": ["对象"],
            "selection_terms": ["芯片"],
            "evidence_types": ["chip_design"],
            "retrieval_hints": ["设计披露"],
            "milestones": [],
            "inference_rules": [],
        }
        with self.assertRaises(ValidationError):
            TechnologyTemplate.model_validate({**base, "selection_terms": []})
        with self.assertRaises(ValidationError):
            TechnologyTemplate.model_validate({**base, "selection_terms": ["芯片", " 芯片 "]})


if __name__ == "__main__":
    unittest.main()
