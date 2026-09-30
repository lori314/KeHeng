"""Deterministic identities for logical sources, versions, chunks and citations."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.knowledge.contracts import (
    CitationLocator,
    Company,
    Source,
    SourceType,
    SourceVersion,
)


class UnstableSourceIdentityError(ValueError):
    """Raised when a source has no durable identifier or content fingerprint."""


def company_for_name(enterprise_name: str) -> Company:
    """Return the shared unresolved company identity for a normalized name."""

    normalized_name = " ".join(unicodedata.normalize("NFKC", enterprise_name).split())
    digest = hashlib.sha256(normalized_name.casefold().encode("utf-8")).hexdigest()[:32]
    return Company(company_id=f"co_{digest}", canonical_name=normalized_name)


def canonicalize_url(url: str) -> str:
    """Apply conservative URL canonicalization without dropping query values."""

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("canonical_url must not contain embedded credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("canonical_url contains an invalid port") from exc

    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, hostname, path, parsed.query, ""))


def _content_hash(value: str | None) -> str:
    if value is None or not re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
        raise UnstableSourceIdentityError("a valid SHA-256 content hash is required")
    return value.strip().lower()


def source_id_for(
    source_type: SourceType | str,
    *,
    external_id: str | None = None,
    canonical_url: str | None = None,
    content_sha256: str | None = None,
) -> str:
    """Return a logical source identity, independent of task and document IDs."""

    kind = SourceType(source_type)
    if external_id and external_id.strip():
        identity = f"external:{kind.value}:{external_id.strip()}"
    elif canonical_url and canonical_url.strip():
        identity = f"url:{canonicalize_url(canonical_url)}"
    elif kind == SourceType.UPLOADED_DOCUMENT:
        identity = f"uploaded_sha256:{_content_hash(content_sha256)}"
    else:
        raise UnstableSourceIdentityError(
            "source requires external_id, canonical_url, or uploaded-document content_sha256"
        )
    return _prefixed_hash("src", identity)


def source_version_id_for(source_id: str, content_sha256: str) -> str:
    """Return a content snapshot identity; retrieval time is intentionally absent."""

    digest = _content_hash(content_sha256)
    return _prefixed_hash("sv", f"{source_id}\0{digest}")


def source_version_for(
    source: Source,
    content_sha256: str,
    *,
    retrieved_at: datetime | None = None,
    published_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    supersedes: str | None = None,
    is_current: bool = True,
) -> SourceVersion:
    digest = _content_hash(content_sha256)
    expected_source_id = source_id_for(
        source.source_type,
        external_id=source.external_id,
        canonical_url=source.canonical_url,
        content_sha256=digest,
    )
    if source.source_id != expected_source_id:
        raise ValueError("Source.source_id does not match its stable identity inputs")
    return SourceVersion(
        source_version_id=source_version_id_for(source.source_id, digest),
        source_id=source.source_id,
        content_sha256=digest,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        published_at=published_at,
        metadata=dict(metadata or {}),
        is_current=is_current,
        supersedes=supersedes,
    )


def knowledge_chunk_id_for(
    source_version_id: str, locator: CitationLocator | Mapping[str, Any], text: str
) -> str:
    normalized_locator = _normalized_locator(locator)
    normalized_text = normalize_text(text)
    payload = json.dumps(
        [source_version_id, normalized_locator, normalized_text],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _prefixed_hash("kch", payload)


def derived_fact_chunk_id_for(source_version_id: str, fact_id: str) -> str:
    """Return the stable chunk identity for one derived technology fact."""

    if not source_version_id.strip() or not fact_id.strip():
        raise ValueError("source_version_id and fact_id are required")
    material = f"derived_fact\0technology_fact\0{source_version_id}\0{fact_id}"
    return _prefixed_hash("kch", material)


def ensure_unique_chunk_ids(chunk_ids: Iterable[str]) -> None:
    """Reject ambiguous duplicate chunk identities before persistence/indexing."""

    identifiers = list(chunk_ids)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate KnowledgeChunk IDs in one source-version upsert batch")


def citation_id_for(
    source_version_id: str,
    locator: CitationLocator | Mapping[str, Any],
    excerpt: str,
) -> str:
    normalized_locator = _normalized_locator(locator)
    normalized_excerpt = normalize_text(excerpt)
    payload = json.dumps(
        [source_version_id, normalized_locator, normalized_excerpt],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _prefixed_hash("cit", payload)


def normalize_text(text: str) -> str:
    """Normalize Unicode compatibility forms and whitespace for stable IDs."""

    return " ".join(unicodedata.normalize("NFKC", text).split())


def _normalized_locator(
    locator: CitationLocator | Mapping[str, Any],
) -> dict[str, Any]:
    payload = (
        locator.model_dump(mode="json", exclude_none=True)
        if isinstance(locator, CitationLocator)
        else dict(locator)
    )
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            if key == "url" and value.strip():
                normalized[key] = canonicalize_url(value)
            else:
                normalized[key] = normalize_text(value)
        else:
            normalized[key] = value
    return normalized


def _prefixed_hash(prefix: str, material: str) -> str:
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"
