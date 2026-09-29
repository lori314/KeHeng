"""Report Agent interface; report rendering belongs to ``app.report``."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.agents.contracts import AgentOutput


class ReportAgentInput(BaseModel):
    task_id: str
    analyses: list[AgentOutput] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    template_version: str = "draft-v0"


class ReportAgentOutput(BaseModel):
    task_id: str
    sections: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportAgent(ABC):
    """Future synthesis component that cannot introduce new facts."""

    @abstractmethod
    async def generate(self, request: ReportAgentInput) -> ReportAgentOutput:
        # TODO(v0.2): synthesize validated findings without adding new evidence.
        raise NotImplementedError("Report Agent inference is not implemented in v0.1")
