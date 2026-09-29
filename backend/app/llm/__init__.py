"""Evidence-grounded LLM contracts and shared provider-neutral adapters."""

from app.llm.api_model import OpenAICompatibleHTTPTransport, OpenAICompatibleProvider
from app.llm.mock import MockLLMProvider
from app.llm.provider import (
    LLMContextChunk,
    LLMExtractionRequest,
    LLMExtractionResult,
    LLMFinding,
    LLMIndicatorExtraction,
    LLMIndustryExtractionResult,
    LLMProvider,
    LLMProviderError,
)
from app.llm.structured import (
    OpenAICompatibleStructuredModel,
    StructuredJSONModel,
    StructuredModelError,
)

__all__ = [
    "LLMContextChunk",
    "LLMExtractionRequest",
    "LLMExtractionResult",
    "LLMIndustryExtractionResult",
    "LLMFinding",
    "LLMIndicatorExtraction",
    "LLMProvider",
    "LLMProviderError",
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "OpenAICompatibleHTTPTransport",
    "OpenAICompatibleStructuredModel",
    "StructuredJSONModel",
    "StructuredModelError",
]
