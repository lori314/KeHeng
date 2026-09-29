"""Persistent local Chroma knowledge base with task-level isolation."""

from abc import ABC, abstractmethod
from pathlib import Path

import chromadb

from app.rag.document_parser import DocumentChunk, DocumentParser, DocumentSource
from app.rag.embedding import EmbeddingProvider, EmbeddingRequest
from app.rag.retrieval import RetrievalQuery, RetrievedEvidence


class KnowledgeBase(ABC):
    """Vector-store-neutral boundary for evidence chunk lifecycle."""

    @abstractmethod
    async def upsert(self, chunks: list[DocumentChunk]) -> int:
        raise NotImplementedError

    @abstractmethod
    async def add_document(
        self, source: DocumentSource, parser: DocumentParser
    ) -> list[DocumentChunk]:
        raise NotImplementedError

    @abstractmethod
    async def query(self, request: RetrievalQuery) -> list[RetrievedEvidence]:
        raise NotImplementedError

    @abstractmethod
    async def delete_document(self, task_id: str, document_id: str) -> int:
        raise NotImplementedError


class ChromaKnowledgeBase(KnowledgeBase):
    """Store evidence locally and enforce ``task_id`` in every query."""

    def __init__(
        self,
        persistence_path: str | Path,
        embedding_provider: EmbeddingProvider,
        collection_name: str = "keheng_evidence_v02",
    ) -> None:
        path = Path(persistence_path).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        self._embedding_provider = embedding_provider
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    async def add_document(
        self, source: DocumentSource, parser: DocumentParser
    ) -> list[DocumentChunk]:
        document = await parser.load(source)
        chunks = await parser.split(document)
        await self.upsert(chunks)
        return chunks

    async def upsert(self, chunks: list[DocumentChunk]) -> int:
        if not chunks:
            return 0
        embedding_batch = await self._embedding_provider.embed(
            EmbeddingRequest(texts=[chunk.text for chunk in chunks])
        )
        if len(embedding_batch.vectors) != len(chunks):
            raise ValueError("Embedding count does not match chunk count")

        ids = [self._storage_id(chunk) for chunk in chunks]
        metadatas = [
            {
                "task_id": chunk.task_id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "page_number": chunk.page_number if chunk.page_number is not None else -1,
                "chunk_id": chunk.chunk_id,
                "locator": chunk.locator,
                "embedding_version": embedding_batch.model_version,
            }
            for chunk in chunks
        ]
        self._collection.upsert(
            ids=ids,
            documents=[chunk.text for chunk in chunks],
            embeddings=embedding_batch.vectors,
            metadatas=metadatas,
        )
        return len(chunks)

    async def query(self, request: RetrievalQuery) -> list[RetrievedEvidence]:
        where = self._build_where(request)
        existing = self._collection.get(where=where, limit=1, include=["metadatas"])
        if not existing.get("ids"):
            return []

        query_embedding = await self._embedding_provider.embed(
            EmbeddingRequest(texts=[request.query])
        )
        result = self._collection.query(
            query_embeddings=query_embedding.vectors,
            n_results=request.top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        evidence: list[RetrievedEvidence] = []
        for storage_id, text, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            page_number = int(metadata["page_number"])
            evidence.append(
                RetrievedEvidence(
                    task_id=str(metadata["task_id"]),
                    document_id=str(metadata["document_id"]),
                    document_name=str(metadata["document_name"]),
                    page_number=page_number if page_number >= 0 else None,
                    chunk_id=str(metadata["chunk_id"]),
                    text=str(text),
                    locator=str(metadata["locator"]),
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                    metadata={"storage_id": str(storage_id)},
                )
            )
        return evidence

    async def delete_document(self, task_id: str, document_id: str) -> int:
        where = {
            "$and": [
                {"task_id": {"$eq": task_id}},
                {"document_id": {"$eq": document_id}},
            ]
        }
        existing = self._collection.get(where=where, include=["metadatas"])
        ids = existing.get("ids") or []
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    @staticmethod
    def _storage_id(chunk: DocumentChunk) -> str:
        return f"{chunk.task_id}:{chunk.document_id}:{chunk.chunk_id}"

    @staticmethod
    def _build_where(request: RetrievalQuery) -> dict[str, object]:
        task_filter: dict[str, object] = {"task_id": {"$eq": request.task_id}}
        if not request.document_ids:
            return task_filter
        return {
            "$and": [
                task_filter,
                {"document_id": {"$in": request.document_ids}},
            ]
        }
