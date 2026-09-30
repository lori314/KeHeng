"""SQLite-backed shared knowledge with a current-version Chroma search index."""

from __future__ import annotations

import asyncio
from pathlib import Path

import chromadb
from pydantic import BaseModel, Field

from app.knowledge.contracts import (
    Company,
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceType,
    SourceVersion,
)
from app.knowledge.identity import ensure_unique_chunk_ids
from app.knowledge.repository import SQLiteKnowledgeRepository, VersionUpsertResult
from app.rag.embedding import EmbeddingProvider, EmbeddingRequest, LocalHashingEmbeddingProvider


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    company_id: str | None = None
    knowledge_layers: list[KnowledgeLayer] | None = None
    source_types: list[SourceType] | None = None


class KnowledgeSearchResult(BaseModel):
    chunk: KnowledgeChunk
    source: Source
    source_version: SourceVersion
    score: float


class SharedKnowledgeBase:
    """Maintain durable knowledge in SQLite and index current chunks in Chroma.

    SQLite is authoritative. Chroma records are rebuildable projections; if a
    process stops between the two writes, repeating ingestion repairs the index.
    """

    collection_name = "keheng_shared_knowledge_v2"

    def __init__(
        self,
        runtime_root: str | Path | None = None,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        repository: SQLiteKnowledgeRepository | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[3] / "runtime" / "knowledge"
        self.runtime_root = Path(runtime_root or default_root).expanduser().resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.repository = repository or SQLiteKnowledgeRepository(
            self.runtime_root / "knowledge.sqlite3"
        )
        self.embedding_provider = embedding_provider or LocalHashingEmbeddingProvider(
            dimensions=1024
        )
        chroma_path = self.runtime_root / "chroma"
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._write_lock = asyncio.Lock()

    async def upsert_source_version(
        self,
        source: Source,
        source_version: SourceVersion,
        chunks: list[KnowledgeChunk],
        *,
        company: Company | None = None,
    ) -> VersionUpsertResult:
        """Persist one snapshot idempotently, then reconcile its Chroma records."""

        ensure_unique_chunk_ids(chunk.chunk_id for chunk in chunks)
        async with self._write_lock:
            outcome = self.repository.upsert_source_version(
                source, source_version, chunks, company=company
            )
            if (
                outcome.became_current
                and
                outcome.previous_current_version_id
                and outcome.previous_current_version_id
                != outcome.source_version.source_version_id
            ):
                self._mark_version_not_current(
                    outcome.previous_current_version_id
                )
            if outcome.became_current:
                await self._upsert_current_vectors(
                    outcome.source, outcome.source_version, list(outcome.chunks)
                )
            return outcome

    async def search(
        self, request: KnowledgeSearchRequest
    ) -> list[KnowledgeSearchResult]:
        query_batch = await self.embedding_provider.embed(
            EmbeddingRequest(texts=[request.query])
        )
        if len(query_batch.vectors) != 1:
            raise ValueError("Embedding provider must return one vector for one query")
        result = self._collection.query(
            query_embeddings=query_batch.vectors,
            n_results=request.top_k,
            where=self._where(request),
            include=["distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        output: list[KnowledgeSearchResult] = []
        for chunk_id, distance in zip(ids, distances, strict=True):
            chunk = self.repository.get_chunk(str(chunk_id))
            if chunk is None or chunk.source_version_id is None:
                continue
            source = self.repository.get_source(chunk.source_id)
            version = self.repository.get_source_version(chunk.source_version_id)
            current = self.repository.get_current_source_version(chunk.source_id)
            if (
                source is None
                or version is None
                or current is None
                or not version.is_current
                or current.source_version_id != version.source_version_id
            ):
                continue
            output.append(
                KnowledgeSearchResult(
                    chunk=chunk,
                    source=source,
                    source_version=version,
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                )
            )
        return output

    def close(self) -> None:
        """Release references; SQLite connections are already scoped per operation."""

        if self._client is not None:
            self._client.close()
        self._collection = None
        self._client = None

    async def _upsert_current_vectors(
        self,
        source: Source,
        source_version: SourceVersion,
        chunks: list[KnowledgeChunk],
    ) -> None:
        if not chunks:
            return
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        existing = self._collection.get(ids=chunk_ids, include=["metadatas"])
        existing_metadata = dict(
            zip(
                existing.get("ids") or [],
                existing.get("metadatas") or [],
                strict=True,
            )
        )
        if all(
            existing_metadata.get(chunk.chunk_id, {}).get("is_current") is True
            and existing_metadata[chunk.chunk_id].get("source_version_id")
            == source_version.source_version_id
            for chunk in chunks
        ):
            return

        embeddings = await self.embedding_provider.embed(
            EmbeddingRequest(texts=[chunk.text for chunk in chunks])
        )
        if len(embeddings.vectors) != len(chunks):
            raise ValueError("Embedding count does not match KnowledgeChunk count")
        metadatas = [
            self._vector_metadata(chunk, source, source_version)
            for chunk in chunks
        ]
        self._collection.upsert(
            ids=chunk_ids,
            documents=[chunk.text for chunk in chunks],
            embeddings=embeddings.vectors,
            metadatas=metadatas,
        )

    def _mark_version_not_current(self, source_version_id: str) -> None:
        chunks = self.repository.list_chunks_for_version(source_version_id)
        if not chunks:
            return
        ids = [chunk.chunk_id for chunk in chunks]
        existing = self._collection.get(ids=ids, include=["metadatas"])
        found_ids = existing.get("ids") or []
        metadatas = existing.get("metadatas") or []
        if found_ids:
            self._collection.update(
                ids=found_ids,
                metadatas=[{**metadata, "is_current": False} for metadata in metadatas],
            )

    @staticmethod
    def _vector_metadata(
        chunk: KnowledgeChunk, source: Source, source_version: SourceVersion
    ) -> dict[str, str | bool]:
        metadata: dict[str, str | bool] = {
            "chunk_id": chunk.chunk_id,
            "source_id": source.source_id,
            "source_version_id": source_version.source_version_id,
            "knowledge_layer": chunk.knowledge_layer.value,
            "source_type": source.source_type.value,
            "is_current": True,
        }
        if chunk.company_id is not None:
            metadata["company_id"] = chunk.company_id
        return metadata

    @staticmethod
    def _where(request: KnowledgeSearchRequest) -> dict[str, object]:
        conditions: list[dict[str, object]] = [{"is_current": {"$eq": True}}]
        if request.company_id is not None:
            conditions.append({"company_id": {"$eq": request.company_id}})
        if request.knowledge_layers:
            layers = list(dict.fromkeys(layer.value for layer in request.knowledge_layers))
            conditions.append(
                {"knowledge_layer": {"$eq": layers[0]}}
                if len(layers) == 1
                else {"knowledge_layer": {"$in": layers}}
            )
        if request.source_types:
            source_types = list(dict.fromkeys(item.value for item in request.source_types))
            conditions.append(
                {"source_type": {"$eq": source_types[0]}}
                if len(source_types) == 1
                else {"source_type": {"$in": source_types}}
            )
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}
