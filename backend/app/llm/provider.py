"""Provider-neutral contracts for evidence-grounded LLM extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMProviderError(RuntimeError):
    """Raised when a provider cannot return a valid structured extraction.

    ``category`` is deliberately machine-readable so experiment reports can
    distinguish transport failures from model/schema failures without exposing
    credentials or provider internals.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
        diagnostics: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.diagnostics = diagnostics or []


class LLMContextChunk(BaseModel):
    """A retrieved chunk exposed to an extraction provider."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^E\d+$")
    document_name: str
    page_number: int | None
    chunk_id: str
    excerpt: str
    retrieval_score: float


class LLMExtractionRequest(BaseModel):
    """Provider input; it contains evidence, never an unconstrained corpus."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    enterprise_name: str
    chunks: list[LLMContextChunk]
    prompt: str
    context_assembly: dict[str, Any] = Field(default_factory=dict)


class LLMIndicatorExtraction(BaseModel):
    """One indicator observation returned by an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    score: int | None = Field(default=None, ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class LLMIndustryExtractionResult(BaseModel):
    """Industry-only structured extraction; no aggregate score is allowed."""

    model_config = ConfigDict(extra="forbid")

    industry_indicators: dict[str, LLMIndicatorExtraction]


class LLMFinding(BaseModel):
    """A provider finding whose evidence IDs are validated by the Agent."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class LLMExtractionResult(BaseModel):
    """Structured extraction only; deliberately has no final technology score."""

    model_config = ConfigDict(extra="forbid")

    technology_summary: str = Field(min_length=1)
    summary_status: Literal["supported", "insufficient_evidence"]
    summary_evidence_ids: list[str]
    technology_indicators: dict[str, LLMIndicatorExtraction]
    strengths: list[LLMFinding] = Field(default_factory=list)
    risks: list[LLMFinding] = Field(default_factory=list)


class LLMProvider(ABC):
    """Replaceable boundary between the Technology Agent and a model."""

    name: str
    implementation_version: str

    @abstractmethod
    async def extract(self, request: LLMExtractionRequest) -> LLMExtractionResult:
        raise NotImplementedError

    async def extract_industry(
        self, request: LLMExtractionRequest
    ) -> LLMIndustryExtractionResult:
        raise LLMProviderError(
            "Provider does not implement industry extraction",
            category="unsupported_operation",
        )


def model_dump_payload(result: LLMExtractionResult) -> dict[str, Any]:
    """Provide a stable serialization helper for provider adapters and tests."""

    return result.model_dump(mode="json")
