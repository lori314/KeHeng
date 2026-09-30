"""Shared structured JSON completion contract and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class StructuredModelError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        raw_output: str | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.raw_output = raw_output
        self.diagnostics = diagnostics or []


class StructuredJSONModel(Protocol):
    async def complete_json(
        self, system_prompt: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]: ...


class OpenAICompatibleStructuredModel:
    """One reusable HTTP adapter for structured calls from any domain module."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        timeout: float = 60,
        enable_thinking: bool | None = None,
    ) -> None:
        if not (endpoint.strip() and model.strip() and api_key.strip()):
            raise StructuredModelError(
                "provider_not_configured", "Structured LLM provider is not configured"
            )
        self.endpoint = endpoint.strip()
        self.model = model.strip()
        self._api_key = api_key.strip()
        self.timeout = timeout
        self.enable_thinking = enable_thinking

    async def complete_json(
        self, system_prompt: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(
            self._complete_sync, system_prompt, payload, {"type": "json_object"}
        )

    async def complete_json_schema(
        self,
        system_prompt: str,
        payload: Mapping[str, Any],
        *,
        schema_name: str,
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Request strict JSON Schema output from compatible providers."""
        import asyncio

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", schema_name)[:64] or "response"
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": safe_name,
                "strict": True,
                "schema": dict(schema),
            },
        }
        return await asyncio.to_thread(
            self._complete_sync, system_prompt, payload, response_format
        )

    def _complete_sync(
        self,
        system_prompt: str,
        payload: Mapping[str, Any],
        response_format: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        }
        if self.enable_thinking is not None:
            request_payload["enable_thinking"] = self.enable_thinking
        endpoint = self.endpoint.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        request = Request(
            endpoint,
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                response_bytes = response.read()
        except HTTPError as exc:
            category = "authentication" if exc.code in {401, 403} else "model_http_error"
            raise StructuredModelError(category, f"Structured LLM returned HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise StructuredModelError("timeout", "Structured LLM timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise StructuredModelError("timeout", "Structured LLM timed out") from exc
            raise StructuredModelError("network", "Structured LLM network request failed") from exc
        except OSError as exc:
            raise StructuredModelError("network", "Structured LLM network request failed") from exc
        try:
            body = response_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StructuredModelError(
                "invalid_response", "Structured LLM response is not valid UTF-8"
            ) from exc
        try:
            response_data = json.loads(body)
            content = response_data["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise StructuredModelError(
                "invalid_response", "Structured LLM returned invalid JSON", raw_output=body
            ) from exc
        if not isinstance(parsed, dict):
            raise StructuredModelError(
                "invalid_response",
                "Structured LLM output must be a JSON object",
                raw_output=str(content),
            )
        return parsed
