"""Finance due-diligence Agent interface."""

from app.agents.contracts import AgentInput, AgentOutput, BaseProfessionalAgent


class FinanceAgent(BaseProfessionalAgent):
    """Future financial quality, cash-flow and financing analysis component."""

    name = "finance_agent"

    async def analyze(self, request: AgentInput) -> AgentOutput:
        # TODO(v0.2): validate financial periods and units before analysis.
        raise NotImplementedError("Finance Agent inference is not implemented in v0.1")
