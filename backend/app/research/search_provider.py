"""Search provider interface and explicit provider failure categories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from app.research.contracts import SearchRequest, SearchResult


SearchFailureCategory = Literal[
    "provider_not_configured",
    "authentication",
    "rate_limit",
    "timeout",
    "network",
    "invalid_response",
]


class SearchProviderError(RuntimeError):
    def __init__(
        self,
        category: SearchFailureCategory,
        message: str,
        *,
        status_code: int | None = None,
        partial_results: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.partial_results = partial_results or {}


class WebSearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, request: SearchRequest) -> list[SearchResult]:
        raise NotImplementedError

    async def extract(self, urls: list[str]) -> dict[str, str]:
        raise SearchProviderError(
            "invalid_response", f"{self.name} does not support URL extraction"
        )
