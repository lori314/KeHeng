"""Deterministically select a diverse, bounded set of semantic evidence chunks."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass

from app.knowledge.contracts import KnowledgeChunk
from app.knowledge.semantic.contracts import (
    SemanticEvidenceSelectionReport,
    SourceQuality,
)

SEMANTIC_MAX_INPUT_CHARS = 48_000
SEMANTIC_MAX_CHUNKS = 18
SEMANTIC_MAX_CHARS_PER_CHUNK = 3_200
SEMANTIC_MAX_CHUNKS_PER_SOURCE = 2

_QUALITY_ORDER = {
    "authoritative_public_record": 0,
    "first_party": 1,
    "academic_or_patent": 2,
    "third_party": 3,
    "weak_web": 4,
    "snippet_only": 5,
}
_NEAR_DUPLICATE_THRESHOLD = 0.93
_NGRAM_SIZE = 5
_MIN_NGRAM_TEXT = 100


@dataclass(frozen=True)
class SemanticEvidenceItem:
    """Original provenance-bearing chunk and its bounded model-facing text."""

    chunk: KnowledgeChunk
    semantic_text: str
    quality: SourceQuality


@dataclass(frozen=True)
class SemanticEvidenceSelection:
    items: list[SemanticEvidenceItem]
    report: SemanticEvidenceSelectionReport

    @property
    def original_chunks(self) -> list[KnowledgeChunk]:
        return [item.chunk for item in self.items]

    @property
    def model_chunks(self) -> list[KnowledgeChunk]:
        return [
            item.chunk.model_copy(update={"text": item.semantic_text})
            for item in self.items
        ]

    @property
    def quality_by_chunk(self) -> dict[str, dict[str, str]]:
        return {
            item.chunk.chunk_id: {
                "category": item.quality.category,
                "source_type": item.quality.source_type,
                "content_scope": item.quality.content_scope,
                "rationale": item.quality.rationale,
            }
            for item in self.items
        }


def select_semantic_evidence(
    chunks: list[KnowledgeChunk],
    quality_by_chunk: dict[str, SourceQuality],
    *,
    max_input_chars: int = SEMANTIC_MAX_INPUT_CHARS,
    max_chunks: int = SEMANTIC_MAX_CHUNKS,
    max_chars_per_chunk: int = SEMANTIC_MAX_CHARS_PER_CHUNK,
    max_chunks_per_source: int = SEMANTIC_MAX_CHUNKS_PER_SOURCE,
) -> SemanticEvidenceSelection:
    """Select by source round, quality, scope, and stable provenance coordinates."""
    if min(max_input_chars, max_chunks, max_chars_per_chunk, max_chunks_per_source) < 1:
        raise ValueError("semantic evidence limits must be positive")

    grouped: dict[str, list[SemanticEvidenceItem]] = {}
    for chunk in chunks:
        quality = quality_by_chunk[chunk.chunk_id]
        item = SemanticEvidenceItem(
            chunk=chunk,
            semantic_text=chunk.text[:max_chars_per_chunk],
            quality=quality,
        )
        grouped.setdefault(chunk.source_id, []).append(item)

    def item_order(item: SemanticEvidenceItem) -> tuple:
        locator = item.chunk.citation.locator
        return (
            _QUALITY_ORDER.get(item.quality.category, len(_QUALITY_ORDER)),
            0 if item.quality.content_scope == "full_content" else 1,
            locator.page_number or 0,
            locator.paragraph_number or 0,
            locator.locator_text or "",
            item.chunk.chunk_id,
        )

    for values in grouped.values():
        values.sort(key=item_order)
    source_order = sorted(
        grouped,
        key=lambda source_id: (item_order(grouped[source_id][0]), source_id),
    )

    selected: list[SemanticEvidenceItem] = []
    selected_chars = 0
    dropped_duplicates: set[str] = set()
    for source_round in range(max_chunks_per_source):
        for source_id in source_order:
            candidates = grouped[source_id]
            if source_round >= len(candidates):
                continue
            candidate = candidates[source_round]
            if any(_is_near_duplicate(candidate.semantic_text, item.semantic_text) for item in selected):
                dropped_duplicates.add(candidate.chunk.chunk_id)
                continue
            if len(selected) >= max_chunks:
                continue
            if selected_chars + len(candidate.semantic_text) > max_input_chars:
                continue
            selected.append(candidate)
            selected_chars += len(candidate.semantic_text)

    selected_ids = {item.chunk.chunk_id for item in selected}
    selected_per_source = Counter(item.chunk.source_id for item in selected)
    dropped_source_cap = 0
    dropped_budget = 0
    for source_id in source_order:
        for candidate in grouped[source_id]:
            chunk_id = candidate.chunk.chunk_id
            if chunk_id in selected_ids or chunk_id in dropped_duplicates:
                continue
            if selected_per_source[source_id] >= max_chunks_per_source:
                dropped_source_cap += 1
            else:
                dropped_budget += 1

    quality_counts = Counter(item.quality.category for item in selected)
    scope_counts = Counter(item.quality.content_scope for item in selected)
    source_type_counts = Counter(item.quality.source_type for item in selected)
    report = SemanticEvidenceSelectionReport(
        available_chunk_count=len(chunks),
        selected_chunk_count=len(selected),
        selected_chunk_ids=[item.chunk.chunk_id for item in selected],
        selected_source_count=len({item.chunk.source_id for item in selected}),
        selected_char_count=selected_chars,
        truncated_chunk_count=sum(len(item.semantic_text) < len(item.chunk.text) for item in selected),
        quality_distribution=dict(sorted(quality_counts.items())),
        content_scope_distribution=dict(sorted(scope_counts.items())),
        source_type_distribution=dict(sorted(source_type_counts.items())),
        dropped_due_to_budget=dropped_budget,
        dropped_due_to_source_cap=dropped_source_cap,
        dropped_as_duplicate=len(dropped_duplicates),
    )
    return SemanticEvidenceSelection(items=selected, report=report)


def _is_near_duplicate(left: str, right: str) -> bool:
    a = _normalized(left)
    b = _normalized(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < _MIN_NGRAM_TEXT:
        return False
    grams_a = _ngrams(a)
    grams_b = _ngrams(b)
    if not grams_a or not grams_b:
        return False
    return len(grams_a & grams_b) / len(grams_a | grams_b) >= _NEAR_DUPLICATE_THRESHOLD


def _ngrams(value: str) -> set[str]:
    if len(value) <= _NGRAM_SIZE:
        return {value}
    return {value[index : index + _NGRAM_SIZE] for index in range(len(value) - _NGRAM_SIZE + 1)}


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def semantic_citation_context(chunk: KnowledgeChunk) -> dict:
    """Keep source coordinates without duplicating the selected body text."""
    citation = chunk.citation.model_dump(mode="json")
    citation.pop("excerpt", None)
    return citation
