"""Bounded, provider-neutral calls validated against typed Pydantic contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import StructuredJSONModel, StructuredModelError

TModel = TypeVar("TModel", bound=BaseModel)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:sk|tvly|key|token)[-_][a-z0-9_-]{8,}\b"),
)


def _safe_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _validation_diagnostics(exc: ValidationError, attempt: int) -> list[dict[str, Any]]:
    diagnostics = []
    for item in exc.errors(include_url=False, include_input=False):
        diagnostics.append(
            {
                "attempt": attempt,
                "location": _safe_text(".".join(str(part) for part in item["loc"]), 120),
                "type": _safe_text(item["type"], 80),
                "message": _safe_text(item["msg"]),
            }
        )
    return diagnostics


async def _invoke(
    model: StructuredJSONModel,
    prompt: str,
    payload: Mapping[str, Any],
    contract: type[TModel],
) -> dict[str, Any]:
    schema_method = getattr(model, "complete_json_schema", None)
    if callable(schema_method):
        return await schema_method(
            prompt,
            payload,
            schema_name=contract.__name__,
            schema=contract.model_json_schema(),
        )
    return await model.complete_json(prompt, payload)


async def complete_contract(
    model: StructuredJSONModel,
    prompt: str,
    payload: dict,
    contract: type[TModel],
    *,
    stage: str,
) -> TModel:
    """Call one stage and allow a single structure-only repair attempt.

    Adapters that expose ``complete_json_schema`` receive the Pydantic JSON
    Schema on both attempts. Legacy adapters and test doubles retain the JSON
    object call and local validation behavior.
    """

    diagnostics: list[dict[str, Any]] = []
    first_category = "schema_failure"
    first_error: Exception | None = None
    try:
        raw = await _invoke(model, prompt, payload, contract)
        return contract.model_validate(raw)
    except ValidationError as exc:
        first_error = exc
        diagnostics = _validation_diagnostics(exc, 1)
    except StructuredModelError as exc:
        if exc.category not in {"invalid_response", "schema_failure", "model_http_error"}:
            raise
        first_error = exc
        first_category = exc.category
        diagnostics = [
            {
                "attempt": 1,
                "location": "response",
                "type": _safe_text(exc.category, 80),
                "message": "Provider response did not satisfy the requested structured format.",
            }
        ]

    repair_prompt = (
        prompt
        + "\n\nThe previous response failed contract validation. Return corrected JSON only; do not add facts."
    )
    repair_payload = {
        **payload,
        # Preserve the established repair payload key without forwarding raw
        # model text, which could contain secrets or unrelated sensitive data.
        "invalid_prior_output": "[omitted for safety]",
        "validation_errors": diagnostics,
        "repair_instruction": (
            "Return one valid JSON object matching the required schema without adding facts."
        ),
    }
    try:
        repaired = await _invoke(model, repair_prompt, repair_payload, contract)
        return contract.model_validate(repaired)
    except ValidationError as exc:
        diagnostics.extend(_validation_diagnostics(exc, 2))
        final_category = "schema_failure"
    except StructuredModelError as exc:
        final_category = "schema_failure" if exc.category in {
            "invalid_response", "schema_failure", "model_http_error"
        } else exc.category
        diagnostics.append(
            {
                "attempt": 2,
                "location": "response",
                "type": _safe_text(exc.category, 80),
                "message": "Repair response did not satisfy the requested structured format.",
            }
        )

    raise StructuredModelError(
        final_category if first_error is not None else first_category,
        f"{stage} response failed after one schema repair",
        diagnostics=diagnostics,
    ) from first_error
