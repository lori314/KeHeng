"""Boundary between API orchestration and the root evaluation package."""

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, Field

from app.agents.contracts import AgentFinding


class EvaluationMethod(StrEnum):
    WEIGHTED_SUM = "weighted_sum"
    MCDA = "mcda"
    AHP = "ahp"
    TRL = "trl"


class EvaluationRequest(BaseModel):
    task_id: str
    findings: list[AgentFinding] = Field(default_factory=list)
    indicator_version: str
    weight_version: str
    method: EvaluationMethod = EvaluationMethod.WEIGHTED_SUM


class EvaluationResponse(BaseModel):
    task_id: str
    status: str
    method: EvaluationMethod
    dimension_scores: dict[str, float | None] = Field(default_factory=dict)
    total_score: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EvaluationGateway(ABC):
    """Versioned scoring interface with room for MCDA, AHP and TRL."""

    @abstractmethod
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        # TODO: expose the reviewed root evaluation engine without changing this API.
        raise NotImplementedError
