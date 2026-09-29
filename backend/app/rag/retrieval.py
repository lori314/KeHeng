"""Evidence retrieval interface."""

from abc import ABC, abstractmethod
from typing import Protocol

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    task_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    document_ids: list[str] = Field(default_factory=list)


class RetrievedEvidence(BaseModel):
    task_id: str
    document_id: str
    document_name: str
    page_number: int | None
    chunk_id: str
    text: str
    locator: str
    score: float
    metadata: dict[str, str] = Field(default_factory=dict)


class Retriever(ABC):
    """Retriever that must enforce task and document filters before search."""

    @abstractmethod
    async def search(self, request: RetrievalQuery) -> list[RetrievedEvidence]:
        raise NotImplementedError


class EvidenceQueryStore(Protocol):
    async def query(self, request: RetrievalQuery) -> list[RetrievedEvidence]: ...


class KnowledgeBaseRetriever(Retriever):
    """Thin retriever that delegates to a task-isolated evidence store."""

    def __init__(self, store: EvidenceQueryStore) -> None:
        self._store = store

    async def search(self, request: RetrievalQuery) -> list[RetrievedEvidence]:
        return await self._store.query(request)
