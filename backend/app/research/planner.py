"""Evidence-grounded, structured Retrieval Planner and LLM adapter."""

from __future__ import annotations

from pathlib import Path

from app.knowledge.semantic.structured_call import complete_contract
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
        return await complete_contract(
            self.model,
            self.prompt,
            {
                "mode": "initial",
                "enterprise_name": enterprise_name,
                "instructions": "Only use the enterprise name as a search seed; do not state remembered facts. Put identity/official registry queries first in company_identity_queries (at most four); put business, technology, product and people queries in their corresponding separate lists for execution only after identity resolution.",
            },
            InitialRetrievalPlan,
            stage="retrieval planner initial",
        )

    async def plan_next(self, context: RetrievalPlanningContext) -> RetrievalPlan:
        return await complete_contract(
            self.model,
            self.prompt,
            {"mode": "iterative", "context": context.model_dump(mode="json")},
            RetrievalPlan,
            stage="retrieval planner iterative",
        )
