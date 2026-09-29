"""Small dependency-free task-scoped BM25 knowledge base."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any

from app.rag.document_parser import DocumentChunk, DocumentParser, DocumentSource
from app.rag.retrieval import RetrievalQuery, RetrievedEvidence


def tokenize(text: str) -> list[str]:
    """NFKC/casefold Latin tokens plus overlapping CJK bigrams."""
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in re.finditer(r"[a-z0-9]+|[\u3400-\u9fff]+", text):
        value = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            if len(value) == 1:
                tokens.append(value)
            else:
                tokens.extend(value[index:index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return tokens


def rank_texts(query: str, texts: list[str], limit: int, *, k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
    query_terms = set(tokenize(query))
    tokenized = [Counter(tokenize(text)) for text in texts]
    lengths = [sum(tokens.values()) for tokens in tokenized]
    average_length = sum(lengths) / len(lengths) if lengths else 1.0
    scores: list[tuple[float, int]] = []
    for term in query_terms:
        df = sum(term in tokens for tokens in tokenized)
        idf = math.log(1 + (len(texts) - df + 0.5) / (df + 0.5))
        for index, tokens in enumerate(tokenized):
            tf = tokens.get(term, 0)
            if tf:
                denominator = tf + k1 * (1 - b + b * lengths[index] / (average_length or 1))
                scores.append((idf * tf * (k1 + 1) / denominator, index))
    totals: dict[int, float] = {}
    for score, index in scores:
        totals[index] = totals.get(index, 0.0) + score
    return sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]


class BM25KnowledgeBase:
    """In-memory KB implementing the same lifecycle and task filter as Chroma."""

    def __init__(self) -> None:
        self._chunks: dict[str, DocumentChunk] = {}

    async def upsert(self, chunks: list[DocumentChunk]) -> int:
        for chunk in chunks:
            self._chunks[self._key(chunk.task_id, chunk.document_id, chunk.chunk_id)] = chunk
        return len(chunks)

    async def add_document(self, source: DocumentSource, parser: DocumentParser) -> list[DocumentChunk]:
        chunks = await parser.split(await parser.load(source))
        await self.upsert(chunks)
        return chunks

    async def delete_document(self, task_id: str, document_id: str) -> int:
        keys = [key for key, chunk in self._chunks.items() if chunk.task_id == task_id and chunk.document_id == document_id]
        for key in keys:
            del self._chunks[key]
        return len(keys)

    async def query(self, request: RetrievalQuery) -> list[RetrievedEvidence]:
        chunks = [chunk for chunk in self._chunks.values() if chunk.task_id == request.task_id and (not request.document_ids or chunk.document_id in request.document_ids)]
        if not chunks:
            return []
        ranked_raw = rank_texts(request.query, [chunk.text for chunk in chunks], request.top_k)
        ranked = [(score, index) for index, score in ranked_raw]
        max_score = ranked[0][0] if ranked else 1.0
        output = []
        for score, index in ranked:
            chunk = chunks[index]
            output.append(RetrievedEvidence(task_id=chunk.task_id, document_id=chunk.document_id, document_name=chunk.document_name, page_number=chunk.page_number, chunk_id=chunk.chunk_id, text=chunk.text, locator=chunk.locator, score=score / max_score if max_score else 0.0, metadata={}))
        return output

    @staticmethod
    def _key(task_id: str, document_id: str, chunk_id: str) -> str:
        return f"{task_id}:{document_id}:{chunk_id}"
