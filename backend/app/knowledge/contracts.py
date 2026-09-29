"""Provider-neutral V2 contracts for companies, sources, citations and knowledge."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompanyResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class Company(BaseModel):
    """A company name can be used before its legal entity is resolved."""

    model_config = ConfigDict(extra="forbid")

    company_id: str | None = None
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    unified_social_credit_code: str | None = None
    official_website: str | None = None
    resolution_status: CompanyResolutionStatus = CompanyResolutionStatus.UNRESOLVED
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceType(StrEnum):
    UPLOADED_DOCUMENT = "uploaded_document"
    COMPANY_OFFICIAL = "company_official"
    GOVERNMENT = "government"
    REGULATORY = "regulatory"
    PATENT = "patent"
    PAPER = "paper"
    STANDARD = "standard"
    NEWS = "news"
    REGISTRY = "registry"
    WEB = "web"
    OTHER = "other"


class Source(BaseModel):
    """Identity and provenance for a source, independent of its storage path."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    source_type: SourceType
    title: str = Field(min_length=1)
    canonical_url: str | None = None
    publisher: str | None = None
    external_id: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CitationLocator(BaseModel):
    """Optional source coordinates for document pages and web passages."""

    model_config = ConfigDict(extra="forbid")

    page_number: int | None = Field(default=None, ge=1)
    locator_text: str | None = None
    section: str | None = None
    heading: str | None = None
    paragraph_number: int | None = Field(default=None, ge=1)
    text_anchor: str | None = None
    url: str | None = None


class Citation(BaseModel):
    """A stable, source-bound quotation and its optional location."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version_id: str | None = None
    source_url: str | None = None
    source_title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    locator: CitationLocator = Field(default_factory=CitationLocator)


class KnowledgeLayer(StrEnum):
    ENTERPRISE = "enterprise"
    DOMAIN = "domain"
    LIFECYCLE = "lifecycle"
    STANDARD = "standard"
    REGULATION = "regulation"
    FINANCIAL_RULE = "financial_rule"
    GENERAL = "general"


class KnowledgeChunk(BaseModel):
    """A source-grounded knowledge unit suitable for future shared indexing."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version_id: str | None = None
    citation: Citation
    company_id: str | None = None
    knowledge_layer: KnowledgeLayer = KnowledgeLayer.GENERAL
    domain_tags: list[str] = Field(default_factory=list)
    template_tags: list[str] = Field(default_factory=list)
    event_type: str | None = None
    event_time: datetime | None = None
    entities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def citation_must_match_source(self) -> "KnowledgeChunk":
        if self.citation.source_id != self.source_id:
            raise ValueError("KnowledgeChunk citation must refer to the same source_id")
        if (
            self.source_version_id is not None
            and self.citation.source_version_id != self.source_version_id
        ):
            raise ValueError(
                "KnowledgeChunk citation must refer to the same source_version_id"
            )
        return self


class SourceVersion(BaseModel):
    """An immutable content snapshot of a logical Source."""

    model_config = ConfigDict(extra="forbid")

    source_version_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    retrieved_at: datetime
    published_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_current: bool = True
    supersedes: str | None = None
