"""Deterministically select a diverse, bounded set of semantic evidence chunks."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
import re

from app.knowledge.contracts import KnowledgeChunk
from app.knowledge.semantic.contracts import (
    FactEvidenceSelectionReport,
    SemanticEvidenceSelectionReport,
    SourceQuality,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry

SEMANTIC_MAX_INPUT_CHARS = 48_000
SEMANTIC_MAX_CHUNKS = 18
SEMANTIC_MAX_CHARS_PER_CHUNK = 3_200
SEMANTIC_MAX_CHUNKS_PER_SOURCE = 2
FACT_EVIDENCE_MAX_CHUNKS = 30
FACT_EVIDENCE_MAX_CHUNKS_PER_SOURCE = 3
FACT_EVIDENCE_MAX_CHARS_PER_CHUNK = 3_200
FACT_EVIDENCE_MAX_TOTAL_CHARS = 80_000
FACT_EVIDENCE_MIN_CONTEXT_CHUNKS = 6

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


@dataclass(frozen=True)
class TemplateFactEvidenceItem:
    chunk: KnowledgeChunk
    semantic_text: str
    quality: SourceQuality
    template_match_score: int


@dataclass(frozen=True)
class TemplateFactEvidenceSelection:
    items: list[TemplateFactEvidenceItem]
    report: FactEvidenceSelectionReport

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


def select_template_fact_evidence(
    chunks: list[KnowledgeChunk],
    quality_by_chunk: dict[str, SourceQuality],
    registry: KnowledgeSemanticRegistry,
    selected_template_ids: list[str],
    *,
    classifier_chunks: list[KnowledgeChunk],
    max_chunks: int = FACT_EVIDENCE_MAX_CHUNKS,
    max_chunks_per_source: int = FACT_EVIDENCE_MAX_CHUNKS_PER_SOURCE,
    max_chars_per_chunk: int = FACT_EVIDENCE_MAX_CHARS_PER_CHUNK,
    max_total_chars: int = FACT_EVIDENCE_MAX_TOTAL_CHARS,
    min_context_chunks: int = FACT_EVIDENCE_MIN_CONTEXT_CHUNKS,
) -> TemplateFactEvidenceSelection:
    """Rerank all raw GENERAL chunks for the templates selected by the classifier."""
    if min(max_chunks, max_chunks_per_source, max_chars_per_chunk, max_total_chars) < 1:
        raise ValueError("fact evidence limits must be positive")
    if min_context_chunks < 0:
        raise ValueError("fact evidence minimum context cannot be negative")

    phrases = _template_selection_phrases(registry, selected_template_ids)
    scored: list[TemplateFactEvidenceItem] = []
    scores_by_id: dict[str, int] = {}
    for chunk in chunks:
        quality = quality_by_chunk[chunk.chunk_id]
        score = _template_match_score(chunk.text, phrases)
        scores_by_id[chunk.chunk_id] = score
        if score > 0:
            scored.append(TemplateFactEvidenceItem(
                chunk=chunk,
                semantic_text=chunk.text[:max_chars_per_chunk],
                quality=quality,
                template_match_score=score,
            ))

    def primary_rank(item: TemplateFactEvidenceItem) -> tuple[int, int, int]:
        return (
            -item.template_match_score,
            _QUALITY_ORDER.get(item.quality.category, len(_QUALITY_ORDER)),
            0 if item.quality.content_scope == "full_content" else 1,
        )

    def stable_rank(item: TemplateFactEvidenceItem) -> tuple:
        locator = item.chunk.citation.locator
        return (
            locator.page_number or 0,
            locator.paragraph_number or 0,
            locator.locator_text or "",
            item.chunk.source_id,
            item.chunk.chunk_id,
        )

    remaining = list(scored)
    selected: list[TemplateFactEvidenceItem] = []
    selected_chars = 0
    source_counts: Counter[str] = Counter()
    dropped_duplicates: set[str] = set()
    dropped_by_source_cap: set[str] = set()

    while remaining and len(selected) < max_chunks:
        eligible = [
            item for item in remaining
            if source_counts[item.chunk.source_id] < max_chunks_per_source
        ]
        if not eligible:
            dropped_by_source_cap.update(item.chunk.chunk_id for item in remaining)
            break
        best_primary = min(primary_rank(item) for item in eligible)
        tied = [item for item in eligible if primary_rank(item) == best_primary]
        candidate = min(
            tied,
            key=lambda item: (source_counts[item.chunk.source_id], stable_rank(item)),
        )
        remaining.remove(candidate)
        if any(_is_near_duplicate(candidate.semantic_text, item.semantic_text) for item in selected):
            dropped_duplicates.add(candidate.chunk.chunk_id)
            continue
        if selected_chars + len(candidate.semantic_text) > max_total_chars:
            continue
        selected.append(candidate)
        source_counts[candidate.chunk.source_id] += 1
        selected_chars += len(candidate.semantic_text)

    selected_ids = {item.chunk.chunk_id for item in selected}
    # Fill sparse template matches with the classifier's broad-context evidence.
    fallback_fill_count = 0
    for chunk in classifier_chunks:
        if len(selected) >= max_chunks or len(selected) >= min_context_chunks:
            break
        if chunk.chunk_id in selected_ids:
            continue
        if source_counts[chunk.source_id] >= max_chunks_per_source:
            continue
        quality = quality_by_chunk[chunk.chunk_id]
        bounded_text = chunk.text[:max_chars_per_chunk]
        if selected_chars + len(bounded_text) > max_total_chars:
            continue
        if any(_is_near_duplicate(bounded_text, item.semantic_text) for item in selected):
            dropped_duplicates.add(chunk.chunk_id)
            continue
        selected.append(TemplateFactEvidenceItem(
            chunk=chunk,
            semantic_text=bounded_text,
            quality=quality,
            template_match_score=scores_by_id.get(chunk.chunk_id, 0),
        ))
        selected_ids.add(chunk.chunk_id)
        source_counts[chunk.source_id] += 1
        selected_chars += len(bounded_text)
        fallback_fill_count += 1

    quality_counts = Counter(item.quality.category for item in selected)
    scope_counts = Counter(item.quality.content_scope for item in selected)
    source_type_counts = Counter(item.quality.source_type for item in selected)
    score_counts = Counter(item.template_match_score for item in scored)
    dropped_budget = sum(
        1 for item in scored
        if item.chunk.chunk_id not in selected_ids
        and item.chunk.chunk_id not in dropped_duplicates
        and item.chunk.chunk_id not in dropped_by_source_cap
    )
    report = FactEvidenceSelectionReport(
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
        dropped_due_to_source_cap=len(dropped_by_source_cap),
        dropped_as_duplicate=len(dropped_duplicates),
        positive_match_candidate_count=len(scored),
        positive_match_selected_count=sum(item.template_match_score > 0 for item in selected),
        fallback_fill_count=fallback_fill_count,
        selected_template_ids=list(selected_template_ids),
        positive_match_score_distribution={str(score): count for score, count in sorted(score_counts.items())},
    )
    return TemplateFactEvidenceSelection(items=selected, report=report)


def _template_selection_phrases(
    registry: KnowledgeSemanticRegistry, selected_template_ids: list[str]
) -> dict[str, int]:
    phrases: dict[str, int] = {}
    fact_types = registry.technology_fact_type_by_id
    for template_id in selected_template_ids:
        template = registry.template_by_id[template_id]
        for term in template.selection_terms:
            normalized = _normalized(term)
            weight = 3 if len(normalized) >= 4 else 2
            phrases[normalized] = max(phrases.get(normalized, 0), weight)
        for item in template.important_objects:
            _add_phrase(phrases, item, 2)
        referenced_fact_types = {
            item.casefold() for item in template.evidence_types
        }
        for milestone in template.milestones:
            _add_phrase(phrases, milestone.label, 2)
            referenced_fact_types.update(item.casefold() for item in milestone.fact_type_hints)
        for fact_type_id in referenced_fact_types:
            fact_type = fact_types.get(fact_type_id)
            if fact_type is not None:
                _add_phrase(phrases, fact_type.description, 2)
    return {phrase: weight for phrase, weight in phrases.items() if phrase}


def _add_phrase(phrases: dict[str, int], phrase: str, weight: int) -> None:
    normalized = _normalized(phrase)
    if normalized:
        phrases[normalized] = max(phrases.get(normalized, 0), weight)


def _template_match_score(text: str, phrases: dict[str, int]) -> int:
    normalized_text = _normalized(text)
    return sum(
        weight for phrase, weight in phrases.items()
        if _contains_phrase(normalized_text, phrase)
    )


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    if not phrase:
        return False
    if any(char.isascii() and char.isalpha() for char in phrase):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
            normalized_text,
        ) is not None
    return phrase in normalized_text


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
