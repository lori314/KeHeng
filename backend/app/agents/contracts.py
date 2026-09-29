"""Shared, evidence-first Agent input and output contracts."""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class FindingType(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    RISK = "risk"
    INFORMATION_GAP = "information_gap"


class EvidenceReference(BaseModel):
    """Stable evidence pointer supplied by the retrieval layer."""

    task_id: str
    document_id: str
    chunk_id: str
    locator: str
    excerpt: str | None = None


class AgentInput(BaseModel):
    """Common input accepted by a professional analysis Agent."""

    task_id: str
    objective: str
    evidence: list[EvidenceReference] = Field(default_factory=list)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    config_version: str = "draft-v0"


class AgentFinding(BaseModel):
    """One explainable finding; evidence IDs must be validated downstream."""

    finding_type: FindingType
    title: str
    conclusion: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainty: str | None = None


class AgentOutput(BaseModel):
    agent_name: str
    status: AgentStatus
    findings: list[AgentFinding] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    model_version: str | None = None


class BaseProfessionalAgent(ABC):
    """Replaceable interface for a single professional analysis domain."""

    name: str

    @abstractmethod
    async def analyze(self, request: AgentInput) -> AgentOutput:
        """Analyze only the supplied evidence and return structured findings."""

        raise NotImplementedError
