"""Transparent, task-local assembly of retrieved chunks for model context."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from app.rag.retrieval import RetrievedEvidence


MAX_CONTEXT_CHUNKS = 8
MAX_CONTEXT_CHARACTERS = 4_160  # 8 parser chunks × 520 characters; mirrors the prior effective limit.


def assemble_context(
    candidates: list[RetrievedEvidence],
    *,
    max_chunks: int = MAX_CONTEXT_CHUNKS,
    max_characters: int = MAX_CONTEXT_CHARACTERS,
    strategy: str = "balanced_sentences_v1",
) -> tuple[list[RetrievedEvidence], dict[str, object]]:
    """Choose full sentence evidence fairly across retrieval-query groups.

    ``legacy`` reproduces the previous global score ordering for offline
    comparisons. Neither strategy sees labels or gold evidence anchors.
    The limit is explicitly characters, not model tokens.
    """
    if strategy not in {"legacy", "balanced_sentences_v1"}:
        raise ValueError(f"Unknown context assembly strategy: {strategy}")
    unique: dict[str, RetrievedEvidence] = {}
    duplicates = 0
    for item in candidates:
        previous = unique.get(item.chunk_id)
        groups = _groups(item)
        if previous is None:
            unique[item.chunk_id] = item.model_copy(
                update={"metadata": {**item.metadata, "retrieval_query_groups": ",".join(map(str, groups))}}
            )
        else:
            duplicates += 1
            merged_groups = sorted(set(_groups(previous)) | set(groups))
            update = {"metadata": {**previous.metadata, "retrieval_query_groups": ",".join(map(str, merged_groups))}}
            if item.score > previous.score:
                update.update({"score": item.score, "text": item.text})
            unique[item.chunk_id] = previous.model_copy(update=update)

    pool = list(unique.values())
    ordered = _balanced_order(pool, max_chunks) if strategy != "legacy" else sorted(pool, key=lambda x: x.score, reverse=True)
    selected: list[RetrievedEvidence] = []
    seen_sentences: set[str] = set()
    total = 0
    duplicate_sentences = 0
    exclusions: list[dict[str, str]] = []
    for item in ordered:
        if len(selected) >= max_chunks:
            exclusions.append({"chunk_id": item.chunk_id, "reason": "max_chunks"})
            continue
        excerpt = item.text if strategy == "legacy" else _unique_full_sentences(item.text, seen_sentences)
        if not excerpt:
            duplicate_sentences += 1
            continue
        if total + len(excerpt) > max_characters:
            # Do not cut a sentence at the character boundary. Keep only complete
            # sentences that fit; otherwise make the budget exclusion explicit.
            if strategy != "legacy":
                excerpt = _fit_complete_sentences(excerpt, max_characters - total)
            if not excerpt or total + len(excerpt) > max_characters:
                exclusions.append({"chunk_id": item.chunk_id, "reason": "character_budget"})
                continue
        selected.append(item.model_copy(update={"text": excerpt}))
        total += len(excerpt)
        if strategy != "legacy":
            for sentence in _sentences(excerpt):
                seen_sentences.add(_normalize(sentence))
    group_counts: dict[str, int] = defaultdict(int)
    for item in selected:
        for group in _groups(item):
            group_counts[str(group)] += 1
    return selected, {
        "strategy": strategy,
        "budget_unit": "characters",
        "max_chunks": max_chunks,
        "max_characters": max_characters,
        "candidate_chunks_after_chunk_id_dedupe": len(pool),
        "selected_chunks": len(selected),
        "selected_characters": total,
        "duplicate_chunk_hits_removed": duplicates,
        "duplicate_only_chunks_skipped": duplicate_sentences,
        "selected_chunks_by_query_group": dict(group_counts),
        "budget_exclusions": exclusions,
    }


def _groups(item: RetrievedEvidence) -> list[int]:
    raw = item.metadata.get("retrieval_query_groups", "")
    result: list[int] = []
    for value in raw.split(","):
        if value.strip().isdigit():
            result.append(int(value.strip()))
    return result or [0]


def _balanced_order(items: list[RetrievedEvidence], max_chunks: int) -> list[RetrievedEvidence]:
    # Reserve half the chunk budget for globally strongest hits, then round-robin
    # remaining slots. This preserves relevance while preventing one query group
    # from consuming the entire context.
    globally_ranked = sorted(items, key=lambda value: value.score, reverse=True)
    seed_count = min(4, max(1, max_chunks // 2), len(globally_ranked))
    seeds = globally_ranked[:seed_count]
    queues: dict[int, deque[RetrievedEvidence]] = defaultdict(deque)
    seed_ids = {item.chunk_id for item in seeds}
    for item in globally_ranked[2:]:
        for group in _groups(item):
            queues[group].append(item)
    groups = sorted(queues)
    output: list[RetrievedEvidence] = list(seeds)
    seen: set[str] = set(seed_ids)
    while groups:
        remaining: list[int] = []
        for group in groups:
            while queues[group]:
                item = queues[group].popleft()
                if item.chunk_id not in seen:
                    seen.add(item.chunk_id)
                    output.append(item)
                    break
            if queues[group]:
                remaining.append(group)
        groups = remaining
    return output


def _sentences(text: str) -> list[str]:
    # A semicolon may join related figures (for example added vs cumulative
    # patent counts), so split only on sentence-final marks or paragraph breaks.
    return [part.strip() for part in re.findall(r"[^。！？!?\n]+[。！？!?]?|[^。！？!?\n]+$", text) if part.strip()]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unique_full_sentences(text: str, seen: set[str]) -> str:
    kept: list[str] = []
    for sentence in _sentences(text):
        fingerprint = _normalize(sentence)
        if fingerprint in seen:
            continue
        kept.append(sentence)
    return "".join(kept)


def _fit_complete_sentences(text: str, available: int) -> str:
    if available <= 0:
        return ""
    result = ""
    for sentence in _sentences(text):
        if len(result) + len(sentence) > available:
            break
        result += sentence
    return result
