"""Provider-neutral search and bounded research contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=300)
    max_results: int = Field(default=5, ge=1, le=20)
    topic: Literal["general", "news"] | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4096)
    content: str = ""
    raw_content: str | None = None
    score: float | None = None
    published_at: datetime | None = None
    provider: str = Field(min_length=1)
    topic: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def require_public_http_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            parsed.port
        except ValueError as exc:
            raise ValueError("search result URL is malformed") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("search result URL must be an HTTP(S) URL without credentials")
        return value


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=300)
    category: str = Field(default="general", min_length=1, max_length=80)


class InitialRetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_identity_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=4)
    business_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=8)
    technology_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=8)
    product_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=8)
    people_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=500)
    target_categories: list[str] = Field(default_factory=list, max_length=12)
    information_gaps: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_initial_query(self) -> "InitialRetrievalPlan":
        if not self.queries():
            raise ValueError("initial retrieval plan must include at least one query")
        return self

    def queries(self) -> list[RetrievalQuery]:
        return [
            *self.company_identity_queries,
            *self.business_queries,
            *self.technology_queries,
            *self.product_queries,
            *self.people_queries,
        ]


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=500)
    target_categories: list[str] = Field(default_factory=list, max_length=12)
    discovered_terms: list[str] = Field(default_factory=list, max_length=30)
    information_gaps: list[str] = Field(default_factory=list, max_length=20)
    should_continue: bool

    @model_validator(mode="after")
    def require_followup_query_when_continuing(self) -> "RetrievalPlan":
        if self.should_continue and not self.new_queries:
            raise ValueError("a continuing retrieval plan must include a new query")
        return self


class RetrievedSourceSummary(BaseModel):
    title: str
    url: str
    snippet: str
    content_scope: Literal["full_content", "search_snippet"]


class RetrievalPlanningContext(BaseModel):
    enterprise_name: str
    aliases: list[str] = Field(default_factory=list)
    current_round: int
    executed_queries: list[str]
    sources: list[RetrievedSourceSummary]
    discovered_terms: list[str]
    information_gaps: list[str]


class ResearchTraceEntry(BaseModel):
    round: int
    queries: list[str]
    results_count: int
    new_source_count: int
    duplicate_source_count: int
    new_terms: list[str]
    information_gaps: list[str]
    planner_reason: str
    stop_reason: str | None = None
    content_retrieval_failures: list[str] = Field(default_factory=list)
    relevant_source_count: int = 0
    irrelevant_source_count: int = 0
    uncertain_source_count: int = 0
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    relevance_diagnostics: dict[str, Any] = Field(default_factory=dict)


class IterativeResearchResult(BaseModel):
    enterprise_name: str
    rounds: int
    queries_executed: list[str]
    sources_found: int
    sources_ingested: int
    new_versions: int
    duplicate_versions: int
    knowledge_chunks_added: int
    trace: list[ResearchTraceEntry]
    remaining_information_gaps: list[str]
    stop_reason: str
    warnings: list[str] = Field(default_factory=list)
    entity_resolution_status: Literal["resolved", "ambiguous", "unresolved"] = "unresolved"
    resolved_canonical_name: str | None = None
    official_website: str | None = None
    official_website_initial: str | None = None
    official_website_enriched_during_research: bool = False
    official_website_enrichment_round: int | None = None
    official_website_enrichment_diagnostic: str | None = None
    official_website_strong_candidate_hosts: list[str] = Field(default_factory=list)
    official_website_external_candidate_hosts: list[str] = Field(default_factory=list)
    identity_evidence_count: int = 0
    identity_candidates: list[dict[str, Any]] = Field(default_factory=list)
    identity_trace: dict[str, Any] | None = None
    relevant_source_count: int = 0
    irrelevant_source_count: int = 0
    uncertain_source_count: int = 0
    source_type_counts: dict[str, int] = Field(default_factory=dict)
