"""Map public search pages into source-versioned, citable knowledge chunks."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from app.knowledge.contracts import (
    Citation,
    CitationLocator,
    Company,
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceVersion,
)
from app.knowledge.identity import (
    canonicalize_url,
    citation_id_for,
    knowledge_chunk_id_for,
    source_id_for,
    source_version_for,
)
from app.research.contracts import SearchResult
from app.research.source_classifier import SourceTypeClassifier

FULL_CONTENT_MIN_CHARS = 500


@dataclass(frozen=True)
class PreparedWebSource:
    source: Source
    source_version: SourceVersion
    chunks: list[KnowledgeChunk]
    company: Company
    content_scope: str


class WebContentChunker:
    """Split Markdown-like page text around headings and paragraph boundaries."""

    def __init__(self, *, max_chars: int = 1400, overlap_chars: int = 160) -> None:
        if max_chars < 300:
            raise ValueError("max_chars must be at least 300")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be non-negative and below max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def split(self, text: str) -> list[tuple[str | None, int, str]]:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        heading: str | None = None
        paragraphs: list[tuple[str | None, int, str]] = []
        current: list[str] = []
        paragraph_number = 0

        def flush() -> None:
            nonlocal paragraph_number, current
            body = " ".join(" ".join(current).split())
            if body:
                paragraph_number += 1
                paragraphs.extend(
                    (heading, paragraph_number, piece)
                    for piece in self._split_long_paragraph(
                        body,
                        max_chars=max(300, self.max_chars - len(heading or "") - 2),
                    )
                )
            current = []

        for line in lines:
            heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading_match:
                flush()
                heading = heading_match.group(1).strip()
            elif not line.strip():
                flush()
            else:
                current.append(line.strip())
        flush()

        chunks: list[tuple[str | None, int, str]] = []
        group_heading: str | None = None
        group_start = 0
        group_parts: list[str] = []
        for section, para_number, paragraph in paragraphs:
            rendered = paragraph
            if group_parts and (
                section != group_heading
                or sum(len(part) + 2 for part in group_parts)
                + len(rendered)
                + len(section or "")
                + 2
                > self.max_chars
            ):
                self._append_chunk(chunks, group_heading, group_start, group_parts)
                group_parts = []
            if not group_parts:
                group_heading = section
                group_start = para_number
            group_parts.append(rendered)
        if group_parts:
            self._append_chunk(chunks, group_heading, group_start, group_parts)

        with_overlap: list[tuple[str | None, int, str]] = []
        for section, para_number, body in chunks:
            if with_overlap and self.overlap_chars and len(body) < self.max_chars:
                previous = with_overlap[-1][2]
                if with_overlap[-1][0] == section:
                    max_prefix = min(self.overlap_chars, self.max_chars - len(body) - 1)
                    prefix = previous[-max_prefix:].strip() if max_prefix > 0 else ""
                    if prefix and not body.startswith(prefix):
                        body = f"{prefix}\n{body}"
            with_overlap.append((section, para_number, body))
        return with_overlap

    def _split_long_paragraph(
        self, paragraph: str, *, max_chars: int | None = None
    ) -> list[str]:
        max_chars = max_chars or self.max_chars
        if len(paragraph) <= max_chars:
            return [paragraph]
        sentences = re.split(r"(?<=[。！？.!?；;])\s*", paragraph)
        pieces: list[str] = []
        current = ""
        for sentence in sentences:
            if not sentence:
                continue
            if len(sentence) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(
                    sentence[start : start + max_chars]
                    for start in range(0, len(sentence), max_chars)
                )
            elif current and len(current) + len(sentence) + 1 > max_chars:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)
        return pieces or [paragraph]

    def _append_chunk(self, target, heading, paragraph_number, parts) -> None:
        body = "\n\n".join(parts).strip()
        text = f"{heading}\n\n{body}" if heading else body
        if text:
            target.append((heading, paragraph_number, text))


def prepare_web_source(
    result: SearchResult,
    *,
    enterprise_name: str,
    company: Company,
    retrieved_at: datetime | None = None,
    chunker: WebContentChunker | None = None,
    source_classifier: SourceTypeClassifier | None = None,
) -> PreparedWebSource:
    canonical_url = canonicalize_url(result.url)
    selected_content = (
        result.raw_content if result.raw_content and result.raw_content.strip() else result.content
    )
    content = _normalize_content(selected_content)
    if not content:
        raise ValueError("Search result has neither page content nor a usable snippet")
    content_scope = (
        "full_content"
        if result.raw_content
        and len(result.raw_content.strip()) >= FULL_CONTENT_MIN_CHARS
        else "search_snippet"
    )
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    classifier = source_classifier or SourceTypeClassifier()
    source_type = classifier.classify(result, company)
    source_type_metadata = {"source_type_registry_version": classifier.registry_version}
    source = Source(
        source_id=source_id_for(source_type, canonical_url=canonical_url),
        source_type=source_type,
        title=result.title.strip() or canonical_url,
        canonical_url=canonical_url,
        metadata={"provider": result.provider, **source_type_metadata},
    )
    source_version = source_version_for(
        source,
        content_sha256,
        retrieved_at=retrieved_at,
        published_at=result.published_at,
        metadata={"provider": result.provider, "content_scope": content_scope, **source_type_metadata},
    )
    chunks: list[KnowledgeChunk] = []
    for section, paragraph_number, body in (chunker or WebContentChunker()).split(content):
        locator = CitationLocator(
            url=canonical_url,
            section=section,
            heading=section,
            paragraph_number=paragraph_number,
            text_anchor=f"p-{paragraph_number}",
        )
        citation = Citation(
            citation_id=citation_id_for(source_version.source_version_id, locator, body),
            source_id=source.source_id,
            source_version_id=source_version.source_version_id,
            source_url=canonical_url,
            source_title=source.title,
            excerpt=body,
            locator=locator,
        )
        chunks.append(
            KnowledgeChunk(
                chunk_id=knowledge_chunk_id_for(source_version.source_version_id, locator, body),
                text=body,
                source_id=source.source_id,
                source_version_id=source_version.source_version_id,
                citation=citation,
                company_id=company.company_id,
                knowledge_layer=KnowledgeLayer.GENERAL,
                entities=[enterprise_name],
                metadata={"content_scope": content_scope, **source_type_metadata},
            )
        )
    return PreparedWebSource(
        source=source,
        source_version=source_version,
        chunks=chunks,
        company=company,
        content_scope=content_scope,
    )


def _normalize_content(content: str) -> str:
    content = unicodedata.normalize("NFKC", content).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in content.splitlines()).strip()
