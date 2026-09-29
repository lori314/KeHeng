"""Evidence-grounded, structured Retrieval Planner and LLM adapter."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.llm import (
    OpenAICompatibleStructuredModel,
    StructuredJSONModel,
    StructuredModelError,
)
from app.research.contracts import (
    InitialRetrievalPlan,
    RetrievalPlan,
    RetrievalPlanningContext,
)


class RetrievalPlanner:
    """Create first-pass and follow-up retrieval plans; never assess company value."""

    def __init__(
        self,
        model: StructuredJSONModel,
        *,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.model = model
        default_prompt_path = Path(__file__).resolve().parents[3] / "prompts" / "retrieval_planner_prompt.md"
        self.prompt = Path(prompt_path or default_prompt_path).read_text(encoding="utf-8")

    async def plan_initial(self, enterprise_name: str) -> InitialRetrievalPlan:
        raw = await self._complete_validated(
            self.prompt,
            {
                "mode": "initial",
                "enterprise_name": enterprise_name,
                "instructions": "Only use the enterprise name as a search seed; do not state remembered facts. Put identity/official registry queries first in company_identity_queries (at most four); put business, technology, product and people queries in their corresponding separate lists for execution only after identity resolution.",
            },
            InitialRetrievalPlan,
        )
        return raw

    async def plan_next(self, context: RetrievalPlanningContext) -> RetrievalPlan:
        return await self._complete_validated(
            self.prompt,
            {"mode": "iterative", "context": context.model_dump(mode="json")},
            RetrievalPlan,
        )

    async def _complete_validated(self, prompt, payload, contract):
        try:
            raw = await self.model.complete_json(prompt, payload)
        except StructuredModelError as exc:
            if exc.category != "invalid_response":
                raise
            raw = {"_invalid_raw_output": exc.raw_output or ""}
            errors = [{"error": "The prior response was not a valid JSON object."}]
        else:
            try:
                return contract.model_validate(raw)
            except ValidationError as exc:
                errors = [
                    {
                        "location": ".".join(str(part) for part in error["loc"]),
                        "message": error["msg"],
                        "type": error["type"],
                    }
                    for error in exc.errors(include_url=False)
                ]

        repair_payload = {
            **payload,
            "invalid_prior_output": raw,
            "validation_errors": errors,
            "repair_instruction": "Return one valid JSON object matching the required schema; do not add facts.",
        }
        try:
            repaired = await self.model.complete_json(
                prompt + "\n\nThe previous response failed contract validation. Return corrected JSON only.",
                repair_payload,
            )
            return contract.model_validate(repaired)
        except (StructuredModelError, ValidationError) as exc:
            raise StructuredModelError(
                "schema_failure", "Retrieval Planner response failed after one repair"
            ) from exc
