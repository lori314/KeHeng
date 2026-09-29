"""SQLite source of truth for shared knowledge and its version history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.knowledge.contracts import Company, KnowledgeChunk, Source, SourceVersion
from app.knowledge.identity import (
    knowledge_chunk_id_for,
    source_id_for,
    source_version_id_for,
)


@dataclass(frozen=True)
class VersionUpsertResult:
    source: Source
    source_version: SourceVersion
    chunks: tuple[KnowledgeChunk, ...]
    previous_current_version_id: str | None
    created_new_version: bool


class SQLiteKnowledgeRepository:
    """Store logical sources, immutable snapshots and complete chunks in SQLite."""

    schema_version = 1

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > self.schema_version:
                raise RuntimeError(
                    f"Knowledge database schema {current_version} is newer than supported "
                    f"schema {self.schema_version}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    company_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_versions (
                    source_version_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    published_at TEXT,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    supersedes TEXT,
                    metadata_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (source_id, content_sha256),
                    UNIQUE (source_version_id, source_id),
                    FOREIGN KEY (source_id) REFERENCES sources(source_id),
                    FOREIGN KEY (supersedes) REFERENCES source_versions(source_version_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_current_version_per_source
                    ON source_versions(source_id) WHERE is_current = 1;
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_version_id TEXT NOT NULL,
                    company_id TEXT,
                    knowledge_layer TEXT NOT NULL,
                    text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES sources(source_id),
                    FOREIGN KEY (source_version_id, source_id)
                        REFERENCES source_versions(source_version_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS knowledge_chunks_by_version
                    ON knowledge_chunks(source_version_id);
                CREATE INDEX IF NOT EXISTS knowledge_chunks_by_company_layer
                    ON knowledge_chunks(company_id, knowledge_layer);
                """
            )
            connection.execute(f"PRAGMA user_version = {self.schema_version}")

    def upsert_company(self, company: Company) -> None:
        if company.company_id is None:
            raise ValueError("company_id is required to persist a Company")
        payload = _json_payload(company)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO companies(company_id, canonical_name, payload_json)
                   VALUES (?, ?, ?)
                   ON CONFLICT(company_id) DO UPDATE SET
                     canonical_name=excluded.canonical_name,
                     payload_json=excluded.payload_json""",
                (company.company_id, company.canonical_name, payload),
            )

    def get_company(self, company_id: str) -> Company | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM companies WHERE company_id = ?",
                (company_id,),
            ).fetchone()
        return Company.model_validate_json(row[0]) if row else None

    def upsert_source_version(
        self,
        source: Source,
        source_version: SourceVersion,
        chunks: list[KnowledgeChunk],
        *,
        company: Company | None = None,
    ) -> VersionUpsertResult:
        """Atomically persist a source snapshot and all complete knowledge chunks."""

        _validate_ingestion(source, source_version, chunks)
        if company is not None:
            if company.company_id is None:
                raise ValueError("company_id is required to persist a Company")
            chunk_company_ids = {
                chunk.company_id for chunk in chunks if chunk.company_id is not None
            }
            if chunk_company_ids and chunk_company_ids != {company.company_id}:
                raise ValueError("Company does not match KnowledgeChunk company_id")

        source_payload = _json_payload(source)
        with self._connection() as connection:
            if company is not None:
                connection.execute(
                    """INSERT INTO companies(company_id, canonical_name, payload_json)
                       VALUES (?, ?, ?)
                       ON CONFLICT(company_id) DO UPDATE SET
                         canonical_name=excluded.canonical_name,
                         payload_json=excluded.payload_json""",
                    (company.company_id, company.canonical_name, _json_payload(company)),
                )

            connection.execute(
                """INSERT INTO sources(source_id, source_type, title, canonical_url, payload_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     source_type=excluded.source_type,
                     title=excluded.title,
                     canonical_url=excluded.canonical_url,
                     payload_json=excluded.payload_json""",
                (
                    source.source_id,
                    source.source_type.value,
                    source.title,
                    source.canonical_url,
                    source_payload,
                ),
            )

            current_row = connection.execute(
                """SELECT source_version_id FROM source_versions
                   WHERE source_id = ? AND is_current = 1""",
                (source.source_id,),
            ).fetchone()
            previous_current_id = current_row[0] if current_row else None
            existing_version = connection.execute(
                """SELECT source_version_id, payload_json FROM source_versions
                   WHERE source_id = ? AND content_sha256 = ?""",
                (source.source_id, source_version.content_sha256.lower()),
            ).fetchone()
            resolved_version = source_version.model_copy(
                update={
                    "is_current": True,
                    "supersedes": source_version.supersedes
                    or (
                        json.loads(existing_version["payload_json"]).get("supersedes")
                        if existing_version is not None
                        else previous_current_id
                        if previous_current_id != source_version.source_version_id
                        else None
                    ),
                }
            )
            old_versions = connection.execute(
                """SELECT source_version_id, payload_json FROM source_versions
                   WHERE source_id = ? AND is_current = 1""",
                (source.source_id,),
            ).fetchall()
            for old_version in old_versions:
                snapshot = json.loads(old_version["payload_json"])
                snapshot["is_current"] = False
                connection.execute(
                    """UPDATE source_versions
                       SET is_current = 0, payload_json = ?
                       WHERE source_version_id = ?""",
                    (
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        old_version["source_version_id"],
                    ),
                )
            connection.execute(
                """INSERT INTO source_versions(
                       source_version_id, source_id, content_sha256, retrieved_at,
                       published_at, is_current, supersedes, metadata_json, payload_json
                   ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(source_id, content_sha256) DO UPDATE SET
                     retrieved_at=excluded.retrieved_at,
                     published_at=excluded.published_at,
                     is_current=1,
                     supersedes=source_versions.supersedes,
                     metadata_json=excluded.metadata_json,
                     payload_json=excluded.payload_json""",
                (
                    resolved_version.source_version_id,
                    resolved_version.source_id,
                    resolved_version.content_sha256.lower(),
                    resolved_version.retrieved_at.isoformat(),
                    resolved_version.published_at.isoformat()
                    if resolved_version.published_at
                    else None,
                    resolved_version.supersedes,
                    _json_payload(resolved_version.metadata),
                    _json_payload(resolved_version),
                ),
            )

            for chunk in chunks:
                connection.execute(
                    """INSERT INTO knowledge_chunks(
                           chunk_id, source_id, source_version_id, company_id,
                           knowledge_layer, text, payload_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(chunk_id) DO UPDATE SET
                         source_id=excluded.source_id,
                         source_version_id=excluded.source_version_id,
                         company_id=excluded.company_id,
                         knowledge_layer=excluded.knowledge_layer,
                         text=excluded.text,
                         payload_json=excluded.payload_json""",
                    (
                        chunk.chunk_id,
                        chunk.source_id,
                        chunk.source_version_id,
                        chunk.company_id,
                        chunk.knowledge_layer.value,
                        chunk.text,
                        _json_payload(chunk),
                    ),
                )

        persisted_version = self.get_source_version(source_version.source_version_id)
        if persisted_version is None:
            raise RuntimeError("Persisted SourceVersion could not be read back")
        return VersionUpsertResult(
            source=source,
            source_version=persisted_version,
            chunks=tuple(chunks),
            previous_current_version_id=previous_current_id,
            created_new_version=existing_version is None,
        )

    def get_source(self, source_id: str) -> Source | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return Source.model_validate_json(row[0]) if row else None

    def get_source_version(self, source_version_id: str) -> SourceVersion | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT payload_json FROM source_versions
                   WHERE source_version_id = ?""",
                (source_version_id,),
            ).fetchone()
        return SourceVersion.model_validate_json(row[0]) if row else None

    def get_current_source_version(self, source_id: str) -> SourceVersion | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT payload_json FROM source_versions
                   WHERE source_id = ? AND is_current = 1""",
                (source_id,),
            ).fetchone()
        return SourceVersion.model_validate_json(row[0]) if row else None

    def list_source_versions(self, source_id: str) -> list[SourceVersion]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM source_versions
                   WHERE source_id = ? ORDER BY retrieved_at, source_version_id""",
                (source_id,),
            ).fetchall()
        return [SourceVersion.model_validate_json(row[0]) for row in rows]

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM knowledge_chunks WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        return KnowledgeChunk.model_validate_json(row[0]) if row else None

    def list_chunks_for_version(self, source_version_id: str) -> list[KnowledgeChunk]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM knowledge_chunks
                   WHERE source_version_id = ? ORDER BY chunk_id""",
                (source_version_id,),
            ).fetchall()
        return [KnowledgeChunk.model_validate_json(row[0]) for row in rows]

    def counts(self, source_id: str | None = None) -> dict[str, int]:
        with self._connection() as connection:
            if source_id is None:
                source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                version_count = connection.execute(
                    "SELECT COUNT(*) FROM source_versions"
                ).fetchone()[0]
                current_count = connection.execute(
                    "SELECT COUNT(*) FROM source_versions WHERE is_current = 1"
                ).fetchone()[0]
                chunk_count = connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks"
                ).fetchone()[0]
            else:
                source_count = connection.execute(
                    "SELECT COUNT(*) FROM sources WHERE source_id = ?", (source_id,)
                ).fetchone()[0]
                version_count = connection.execute(
                    "SELECT COUNT(*) FROM source_versions WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0]
                current_count = connection.execute(
                    """SELECT COUNT(*) FROM source_versions
                       WHERE source_id = ? AND is_current = 1""",
                    (source_id,),
                ).fetchone()[0]
                chunk_count = connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0]
        return {
            "sources": int(source_count),
            "source_versions": int(version_count),
            "current_versions": int(current_count),
            "knowledge_chunks": int(chunk_count),
        }


def _validate_ingestion(
    source: Source,
    source_version: SourceVersion,
    chunks: list[KnowledgeChunk],
) -> None:
    expected_source_id = source_id_for(
        source.source_type,
        external_id=source.external_id,
        canonical_url=source.canonical_url,
        content_sha256=source_version.content_sha256,
    )
    if source.source_id != expected_source_id:
        raise ValueError("Source.source_id does not match its stable identity inputs")
    expected_version_id = source_version_id_for(
        source.source_id, source_version.content_sha256
    )
    if source_version.source_id != source.source_id:
        raise ValueError("SourceVersion.source_id does not match Source")
    if source_version.source_version_id != expected_version_id:
        raise ValueError("SourceVersion ID does not match source_id and content hash")
    for chunk in chunks:
        if chunk.source_id != source.source_id:
            raise ValueError("KnowledgeChunk source_id does not match Source")
        if chunk.source_version_id != source_version.source_version_id:
            raise ValueError("KnowledgeChunk source_version_id is required and must match")
        if chunk.citation.source_version_id != source_version.source_version_id:
            raise ValueError("Citation source_version_id is required and must match")
        expected_chunk_id = knowledge_chunk_id_for(
            source_version.source_version_id, chunk.citation.locator, chunk.text
        )
        if chunk.chunk_id != expected_chunk_id:
            raise ValueError("KnowledgeChunk ID does not match version, locator and text")
        if chunk.citation.citation_id != chunk_citation_id(chunk):
            raise ValueError("Citation ID does not match version, locator and excerpt")


def chunk_citation_id(chunk: KnowledgeChunk) -> str:
    from app.knowledge.identity import citation_id_for

    if chunk.source_version_id is None:
        raise ValueError("source_version_id is required for persistent citations")
    return citation_id_for(
        chunk.source_version_id, chunk.citation.locator, chunk.citation.excerpt
    )


def _json_payload(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
