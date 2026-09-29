"""Tavily Search and Extract adapter using the Python standard library."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pydantic import ValidationError

from app.research.contracts import SearchRequest, SearchResult
from app.research.search_provider import SearchProviderError, WebSearchProvider


class TavilySearchProvider(WebSearchProvider):
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        search_depth: str = "advanced",
        timeout: float = 30,
    ) -> None:
        if not api_key.strip():
            raise SearchProviderError(
                "provider_not_configured", "Tavily API key is not configured"
            )
        if search_depth not in {"basic", "advanced"}:
            raise ValueError("search_depth must be basic or advanced")
        self._api_key = api_key.strip()
        self.search_depth = search_depth
        self.timeout = timeout

    async def search(self, request: SearchRequest) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": request.query,
            "search_depth": self.search_depth,
            "max_results": request.max_results,
            "include_raw_content": "markdown",
        }
        if request.topic:
            payload["topic"] = request.topic
        if request.include_domains:
            payload["include_domains"] = request.include_domains
        if request.exclude_domains:
            payload["exclude_domains"] = request.exclude_domains
        response = await self._post("https://api.tavily.com/search", payload)
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise SearchProviderError(
                "invalid_response", "Tavily search response has no results list"
            )
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                raise SearchProviderError(
                    "invalid_response", "Tavily search result has an invalid shape"
                )
            title = item.get("title")
            url = item.get("url")
            content = item.get("content")
            if not isinstance(title, str) or not isinstance(url, str):
                raise SearchProviderError(
                    "invalid_response", "Tavily search result is missing title or URL"
                )
            try:
                results.append(
                    SearchResult(
                        title=title or url,
                        url=url,
                        content=content if isinstance(content, str) else "",
                        raw_content=(
                            item.get("raw_content")
                            if isinstance(item.get("raw_content"), str)
                            else None
                        ),
                        score=item.get("score") if isinstance(item.get("score"), (float, int)) else None,
                        published_at=_parse_date(item.get("published_date")),
                        provider=self.name,
                        topic=request.topic,
                        metadata={
                            key: item[key]
                            for key in ("favicon", "published_date")
                            if item.get(key) is not None
                        },
                    )
                )
            except ValidationError as exc:
                raise SearchProviderError(
                    "invalid_response", "Tavily search result failed contract validation"
                ) from exc
        return results

    async def extract(self, urls: list[str]) -> dict[str, str]:
        if not urls:
            return {}
        response = await self._post(
            "https://api.tavily.com/extract",
            {
                "api_key": self._api_key,
                "urls": urls,
                "extract_depth": "advanced",
                "format": "markdown",
            },
        )
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise SearchProviderError(
                "invalid_response", "Tavily extract response has no results list"
            )
        extracted: dict[str, str] = {}
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url, content = item.get("url"), item.get("raw_content")
            if isinstance(url, str) and isinstance(content, str) and content.strip():
                extracted[url] = content
        failed_results = response.get("failed_results")
        if (isinstance(failed_results, list) and failed_results) or len(extracted) < len(urls):
            raise SearchProviderError(
                "invalid_response",
                "Tavily could not extract every requested URL",
                partial_results=extracted,
            )
        return extracted

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post_sync, endpoint, payload)

    def _post_sync(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                response_bytes = response.read()
        except HTTPError as exc:
            category = (
                "authentication"
                if exc.code in {401, 403}
                else "rate_limit"
                if exc.code == 429
                else "network"
                if exc.code >= 500
                else "invalid_response"
            )
            raise SearchProviderError(
                category, f"Tavily returned HTTP {exc.code}", status_code=exc.code
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise SearchProviderError("timeout", "Tavily request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise SearchProviderError("timeout", "Tavily request timed out") from exc
            raise SearchProviderError("network", "Tavily network request failed") from exc
        except OSError as exc:
            raise SearchProviderError("network", "Tavily network request failed") from exc
        try:
            raw = response_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SearchProviderError(
                "invalid_response", "Tavily response is not valid UTF-8"
            ) from exc
        try:
            response_data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SearchProviderError(
                "invalid_response", "Tavily response is not valid JSON"
            ) from exc
        if not isinstance(response_data, dict):
            raise SearchProviderError(
                "invalid_response", "Tavily response must be a JSON object"
            )
        return response_data


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
