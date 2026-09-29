"""OpenAI-compatible provider adapter with an explicit real HTTP transport.

The provider remains transport-injectable so the offline application and tests
stay deterministic. Real experiments may opt in to ``from_http``; credentials
are held only in memory and are never serialized or logged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import asyncio
import json
import socket
import time
from typing import Any
from pydantic import ValidationError
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.llm.provider import (
    LLMExtractionRequest,
    LLMExtractionResult,
    LLMIndustryExtractionResult,
    LLMProvider,
    LLMProviderError,
)


APITransport = Callable[[str, str, LLMExtractionRequest], Awaitable[Any]]


class OpenAICompatibleHTTPTransport:
    """Small dependency-free Chat Completions transport for real runs."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 120.0,
        temperature: float = 0.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        json_mode: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_retries = max(0, min(int(max_retries), 3))
        self.backoff_base = max(0.0, float(backoff_base))
        self.json_mode = bool(json_mode)
        self.call_count = 0
        self.last_response: Mapping[str, Any] | None = None
        self.last_content: str | Mapping[str, Any] | None = None
        self.response_history: list[Mapping[str, Any]] = []
        self.content_history: list[str | Mapping[str, Any]] = []
        self.last_error: str | None = None
        self.last_observation: dict[str, Any] = {}
        self.observation_history: list[dict[str, Any]] = []
        self.request_history: list[dict[str, Any]] = []

    async def __call__(
        self, endpoint: str, model: str, request: LLMExtractionRequest
    ) -> Mapping[str, Any]:
        payload = {
            "model": model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": request.prompt},
                {"role": "user", "content": _render_context(request)},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        self.call_count += 1
        started = time.perf_counter()
        self.last_observation = {
            "input_evidence_count": len(request.chunks),
            "input_chars": sum(len(chunk.excerpt) for chunk in request.chunks),
            "prompt_chars": len(request.prompt),
            "attempts": 0,
            "retry_count": 0,
            "first_attempt_success": False,
            "first_attempt_schema_success": False,
            "eventual_success": False,
            "eventual_schema_success": False,
            "schema_success": False,
            "final_error": None,
            "error_category": None,
            "latency_seconds": None,
            "output_chars": None,
            "output_tokens": None,
            "response_format_enabled": self.json_mode,
        }
        for attempt in range(self.max_retries + 1):
            self.last_observation["attempts"] = attempt + 1
            self.request_history.append({"task_id": request.task_id, "enterprise_name": request.enterprise_name, "prompt": request.prompt, "chunks": [chunk.model_dump(mode="json") for chunk in request.chunks], "context_assembly": request.context_assembly, "attempt": attempt + 1})
            try:
                response = await asyncio.to_thread(self._request, endpoint, payload)
                content = _message_content(response)
                self.last_content = content
                self.response_history.append(response)
                self.content_history.append(content)
                parsed = _parse_json_object(content)
                self.last_response = response
                self.last_error = None
                self.last_observation["first_attempt_success"] = attempt == 0
                self.last_observation["eventual_success"] = True
                self.last_observation["output_chars"] = len(content) if isinstance(content, str) else None
                usage = response.get("usage") if isinstance(response, Mapping) else None
                if isinstance(usage, Mapping):
                    self.last_observation["output_tokens"] = usage.get("completion_tokens")
                self._finish_observation(started)
                return parsed
            except LLMProviderError as exc:
                self.last_error = str(exc)
                self.last_observation["error_category"] = exc.category
                self.last_observation["http_status_code"] = exc.status_code
                self.last_observation["final_error"] = str(exc)
                if not exc.retryable or attempt >= self.max_retries:
                    self._finish_observation(started)
                    raise
                self.last_observation["retry_count"] = attempt + 1
                await asyncio.sleep(self.backoff_base * (2**attempt))
            except Exception as exc:  # pragma: no cover - network-specific guard
                error = LLMProviderError(
                    f"OpenAI-compatible HTTP request failed: {type(exc).__name__}",
                    category="other_transport_error",
                )
                self.last_error = str(error)
                self.last_observation["error_category"] = error.category
                self.last_observation["final_error"] = str(error)
                self._finish_observation(started)
                raise error from exc
        raise AssertionError("retry loop must return or raise")

    def _finish_observation(self, started: float) -> None:
        self.last_observation["latency_seconds"] = round(time.perf_counter() - started, 4)
        self.observation_history.append(dict(self.last_observation))

    def _sync_observation(self) -> None:
        if self.observation_history:
            self.observation_history[-1] = dict(self.last_observation)

    def mark_schema_success(self) -> None:
        self.last_observation["schema_success"] = True
        self.last_observation["eventual_schema_success"] = True
        self.last_observation["first_attempt_schema_success"] = self.last_observation.get("attempts") == 1
        self._sync_observation()

    def mark_schema_failure(self, category: str = "schema_failure") -> None:
        self.last_observation["schema_success"] = False
        self.last_observation["eventual_schema_success"] = False
        self.last_observation["first_attempt_schema_success"] = False
        self.last_observation["error_category"] = category
        self._sync_observation()

    def mark_repair(self, *, first_valid: bool, triggered: bool, success: bool, final_status: str) -> None:
        self.last_observation.update(
            {
                "first_response_valid": bool(first_valid),
                "repair_triggered": bool(triggered),
                "repair_success": bool(success),
                "final_status": final_status,
            }
        )
        self._sync_observation()

    def _request(self, endpoint: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        url = endpoint.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            body_lower = body.casefold()
            self.last_observation["http_status_code"] = exc.code
            self.last_observation["billing_or_quota_error"] = any(
                marker in body_lower for marker in ("insufficient_quota", "insufficient balance", "billing hard limit", "out of credits")
            )
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            category = "rate_limit_429" if exc.code == 429 else "http_5xx" if 500 <= exc.code <= 599 else "http_error"
            raise LLMProviderError(
                f"HTTP {exc.code}: {body}",
                category=category,
                retryable=retryable,
                status_code=exc.code,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMProviderError(
                "HTTP request timed out", category="http_timeout", retryable=True
            ) from exc
        except URLError as exc:
            raise LLMProviderError(
                f"connection error: {exc.reason}",
                category="connection_error",
                retryable=True,
            ) from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("HTTP response was not JSON", category="json_syntax_error") from exc
        if not isinstance(value, Mapping):
            raise LLMProviderError("HTTP response must be a JSON object", category="json_syntax_error")
        return value


def _render_context(request: LLMExtractionRequest) -> str:
    chunks = [chunk.model_dump(mode="json") for chunk in request.chunks]
    return (
        f"企业名称：{request.enterprise_name}\n"
        "只返回符合提示词契约的 JSON，不要 Markdown，不要最终综合评分。\n"
        f"检索证据：{json.dumps(chunks, ensure_ascii=False)}"
    )


def _message_content(response: Mapping[str, Any]) -> str | Mapping[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("response has no choices", category="empty_content")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, (str, Mapping)):
        return content
    raise LLMProviderError("response has no message content", category="empty_content")


def _parse_json_object(content: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(content, Mapping):
        return content
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) >= 3 else text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("message content is not valid JSON", category="json_syntax_error") from exc
    if not isinstance(value, Mapping):
        raise LLMProviderError("message JSON must be an object", category="json_syntax_error")
    return value


class OpenAICompatibleProvider(LLMProvider):
    """Adapter contract for an OpenAI-compatible structured-output endpoint."""

    name = "openai-compatible"
    implementation_version = "api-transport-v0.9"

    def __init__(
        self,
        endpoint: str,
        model: str,
        transport: APITransport | None = None,
        repair_enabled: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self._transport = transport
        self.repair_enabled = bool(repair_enabled)

    @classmethod
    def from_http(
        cls,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        timeout: float = 120.0,
        temperature: float = 0.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        json_mode: bool = True,
        repair_enabled: bool = True,
    ) -> "OpenAICompatibleProvider":
        """Create an explicit real HTTP provider without changing Agent APIs."""

        return cls(
            endpoint,
            model,
            transport=OpenAICompatibleHTTPTransport(
                api_key=api_key,
                timeout=timeout,
                temperature=temperature,
                max_retries=max_retries,
                backoff_base=backoff_base,
                json_mode=json_mode,
            ),
            repair_enabled=repair_enabled,
        )

    async def extract(self, request: LLMExtractionRequest) -> LLMExtractionResult:
        return await self._extract_contract(request, LLMExtractionResult, "technology")

    async def extract_industry(
        self, request: LLMExtractionRequest
    ) -> LLMIndustryExtractionResult:
        return await self._extract_contract(request, LLMIndustryExtractionResult, "industry")

    async def _extract_contract(self, request: LLMExtractionRequest, contract: Any, label: str) -> Any:
        """Validate once, then perform at most one formatting-only repair."""
        if self._transport is None:
            raise LLMProviderError(
                "API model transport is not configured; no external call was made",
                category="provider_not_configured",
            )
        first_observation: dict[str, Any] = {}
        try:
            raw = await self._transport(self.endpoint, self.model, request)
            first_observation = dict(getattr(self._transport, "last_observation", {}))
            result = contract.model_validate(raw)
            if hasattr(self._transport, "mark_schema_success"):
                self._transport.mark_schema_success()
            if hasattr(self._transport, "mark_repair"):
                self._transport.mark_repair(first_valid=True, triggered=False, success=False, final_status="first_pass_success")
            return result
        except Exception as first_exc:
            category = getattr(first_exc, "category", None) or getattr(self._transport, "last_observation", {}).get("error_category") or "schema_failure"
            if isinstance(first_exc, LLMProviderError) and category not in {"schema_failure", "json_syntax_error"}:
                raise
            if not self.repair_enabled or category not in {"schema_failure", "json_syntax_error"}:
                if isinstance(first_exc, LLMProviderError):
                    raise
                raise LLMProviderError(
                    f"API model {label} response violates extraction contract",
                    category="schema_failure",
                ) from first_exc
            repaired_request = request.model_copy(
                update={
                    "prompt": _repair_prompt(
                        request.prompt,
                        getattr(self._transport, "last_content", None),
                        _safe_validation_errors(first_exc),
                    )
                }
            )
            repair_validation_errors: list[dict[str, str]] = []
            try:
                repaired_raw = await self._transport(self.endpoint, self.model, repaired_request)
                result = contract.model_validate(repaired_raw)
                if hasattr(self._transport, "mark_schema_success"):
                    self._transport.mark_schema_success()
                self._merge_repair_observation(first_observation, success=True, final_status="repair_success")
                if isinstance(getattr(self._transport, "last_observation", None), dict):
                    self._transport.last_observation["first_validation_errors"] = _safe_validation_errors(first_exc)
                    if hasattr(self._transport, "_sync_observation"):
                        self._transport._sync_observation()
                return result
            except Exception as repair_exc:
                repair_validation_errors = _safe_validation_errors(repair_exc)
                if hasattr(self._transport, "mark_schema_failure"):
                    self._transport.mark_schema_failure("schema_failure")
                if isinstance(getattr(self._transport, "last_observation", None), dict):
                    self._transport.last_observation["first_validation_errors"] = _safe_validation_errors(first_exc)
                    self._transport.last_observation["repair_validation_errors"] = _safe_validation_errors(repair_exc)
                    if hasattr(self._transport, "_sync_observation"):
                        self._transport._sync_observation()
                self._merge_repair_observation(first_observation, success=False, final_status="failed")
                if isinstance(repair_exc, LLMProviderError) and getattr(repair_exc, "category", None) not in {"schema_failure", "json_syntax_error"}:
                    raise
                raise LLMProviderError(
                    f"API model {label} response violates extraction contract after one repair",
                    category="schema_failure",
                    diagnostics=[*_safe_validation_errors(first_exc), *repair_validation_errors],
                ) from repair_exc

    def _merge_repair_observation(
        self, first: Mapping[str, Any], *, success: bool, final_status: str
    ) -> None:
        if not hasattr(self._transport, "last_observation"):
            return
        current = dict(getattr(self._transport, "last_observation", {}))
        current.update(
            {
                "first_response_valid": False,
                "repair_triggered": True,
                "repair_success": success,
                "final_status": final_status,
                "first_call_observation": dict(first),
            }
        )
        self._transport.last_observation = current
        if hasattr(self._transport, "_sync_observation"):
            self._transport._sync_observation()


def _safe_validation_errors(exc: Exception) -> list[dict[str, str]]:
    if isinstance(exc, ValidationError):
        return [
            {
                "field": ".".join(str(part) for part in item.get("loc", ())),
                "type": str(item.get("type", "validation_error")),
                "message": str(item.get("msg", "validation failed")),
            }
            for item in exc.errors(include_input=False)
        ]
    return [{"field": "response", "type": type(exc).__name__, "message": "response did not match the required JSON structure"}]


def _repair_prompt(prompt: str, previous: Any, errors: list[dict[str, str]] | None = None) -> str:
    previous_text = previous if isinstance(previous, str) else json.dumps(previous, ensure_ascii=False)
    previous_text = previous_text[:24000]
    return (
        f"{prompt}\n\n上一轮输出不符合JSON Schema。请根据以下字段错误修正结构；不得新增事实、分数或证据编号。"
        f"校验错误：{json.dumps(errors or [], ensure_ascii=False)}。"
        "若某字段无法在原输出及给定证据中合法修复，应按契约返回空值/空数组，而不是猜测补齐。"
        "只返回一个JSON对象，禁止Markdown、代码块和解释文字。\n"
        f"上一轮输出：{previous_text}"
    )
