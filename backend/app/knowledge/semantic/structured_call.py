"""One bounded schema-repair policy shared by semantic LLM steps."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import StructuredJSONModel, StructuredModelError

TModel = TypeVar("TModel", bound=BaseModel)


async def complete_contract(
    model: StructuredJSONModel,
    prompt: str,
    payload: dict,
    contract: type[TModel],
    *,
    stage: str,
) -> TModel:
    """Call one stage and allow a single structure-only repair attempt."""

    raw = None
    validation_error: Exception | None = None
    try:
        raw = await model.complete_json(prompt, payload)
        return contract.model_validate(raw)
    except StructuredModelError as exc:
        if exc.category != "invalid_response":
            raise
        raw = {"_invalid_raw_output": exc.raw_output or ""}
        validation_error = exc
    except ValidationError as exc:
        validation_error = exc

    errors = (
        [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in validation_error.errors(include_url=False)
        ]
        if isinstance(validation_error, ValidationError)
        else [{"error": "The prior response was not valid JSON."}]
    )
    try:
        repaired = await model.complete_json(
            prompt + "\n\nThe previous response failed schema validation. Return corrected JSON only; do not add facts.",
            {
                **payload,
                "invalid_prior_output": raw,
                "validation_errors": errors,
                "repair_instruction": "Return one valid JSON object matching the required schema without adding facts.",
            },
        )
        return contract.model_validate(repaired)
    except (StructuredModelError, ValidationError) as exc:
        raise StructuredModelError(
            "schema_failure", f"{stage} response failed after one schema repair"
        ) from exc
