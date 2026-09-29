"""Evidence-bound company identity resolution and page-to-company relevance gating."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.contracts import Company
from app.knowledge.identity import canonicalize_url
from app.knowledge.semantic.structured_call import complete_contract
from app.llm import StructuredJSONModel, StructuredModelError
from app.research.contracts import SearchResult


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityCandidate(StrictModel):
    canonical_name: str = Field(min_length=1, max_length=300)
    official_website: str | None = None
    unified_social_credit_code: str | None = None
    evidence_urls: list[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=1, max_length=800)


class EntityResolutionResult(StrictModel):
    status: Literal["resolved", "ambiguous", "unresolved"]
    input_name: str = Field(min_length=1, max_length=300)
    canonical_name: str | None = Field(default=None, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    official_website: str | None = None
    unified_social_credit_code: str | None = None
    evidence_urls: list[str] = Field(default_factory=list, max_length=20)
    identity_candidates: list[IdentityCandidate] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1200)


class IdentityResolutionTrace(StrictModel):
    queries: list[str] = Field(default_factory=list)
    results_count: int = 0
    status: Literal["resolved", "ambiguous", "unresolved"]
    canonical_name: str | None = None
    official_website: str | None = None
    evidence_count: int = 0
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    reason: str


class SourceRelevanceDecision(StrictModel):
    status: Literal["relevant", "irrelevant", "uncertain"]
    url: str = Field(min_length=1, max_length=4096)
    evidence: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=800)


class RelevanceOutput(StrictModel):
    decisions: list[SourceRelevanceDecision] = Field(max_length=100)


class IdentityResolver:
    def __init__(self, model: StructuredJSONModel, prompt_path: str | Path | None = None):
        self.model = model
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "entity_resolver_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")

    async def resolve(self, input_name: str, results: list[SearchResult]) -> EntityResolutionResult:
        if not results:
            return EntityResolutionResult(status="unresolved", input_name=input_name, reason="身份检索没有返回可核验结果。")
        contexts = [_identity_context(item) for item in results]
        output = await complete_contract(self.model, self.prompt, {"input_name": input_name, "identity_search_results": contexts}, EntityResolutionResult, stage="entity resolver")
        if output.input_name != input_name:
            raise StructuredModelError("invalid_identity_claim", "Entity Resolver changed the input company name")
        by_url = {canonicalize_url(x.url): x for x in results}
        _validate_urls(output.evidence_urls, by_url, required=output.status == "resolved")
        for candidate in output.identity_candidates:
            _validate_candidate(candidate, by_url)
        if output.status == "ambiguous":
            if len(output.identity_candidates) < 2:
                raise StructuredModelError("invalid_identity_claim", "Ambiguous identity requires at least two evidence-bound candidates")
            return output
        if output.status == "unresolved":
            if output.canonical_name or output.official_website or output.unified_social_credit_code or output.aliases:
                raise StructuredModelError("invalid_identity_claim", "Unresolved identity cannot contain asserted identity fields")
            return output
        if not output.canonical_name:
            raise StructuredModelError("invalid_identity_claim", "Resolved identity requires canonical_name")
        _require_claim_evidence(output.canonical_name, output.evidence_urls, by_url)
        for alias in output.aliases:
            _require_claim_evidence(alias, output.evidence_urls, by_url)
        if output.official_website:
            _validate_website(output.official_website, output.evidence_urls, by_url)
        if output.unified_social_credit_code:
            digits = re.sub(r"\s", "", output.unified_social_credit_code).upper()
            if not re.fullmatch(r"[0-9A-Z]{18}", digits):
                raise StructuredModelError("invalid_identity_claim", "Unified social credit code is malformed")
            _require_claim_evidence(digits, output.evidence_urls, by_url, casefold=False)
        return output


class EnterpriseRelevanceGate:
    """Batch relevance check; only relevant decisions are eligible for ingestion."""

    def __init__(self, model: StructuredJSONModel, prompt_path: str | Path | None = None):
        self.model = model
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "enterprise_relevance_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")

    async def assess(self, company: Company, results: list[SearchResult]) -> list[SourceRelevanceDecision]:
        if not results:
            return []
        pending, decisions = [], []
        for result in results:
            if _is_official_host(result.url, company.official_website):
                decisions.append(SourceRelevanceDecision(status="relevant", url=canonicalize_url(result.url), evidence="", reason="来源 host 与已解析的企业官网 host 一致或为其子域名。"))
            else:
                pending.append(result)
        if pending:
            payload = {
                "resolved_company": {"canonical_name": company.canonical_name, "aliases": company.aliases, "official_website": company.official_website, "unified_social_credit_code": company.unified_social_credit_code},
                "pages": [_identity_context(item) for item in pending],
            }
            output = await complete_contract(self.model, self.prompt, payload, RelevanceOutput, stage="enterprise relevance gate")
            by_url = {canonicalize_url(x.url): x for x in pending}
            seen = set()
            for item in output.decisions:
                url = canonicalize_url(item.url)
                if url not in by_url or url in seen:
                    raise StructuredModelError("invalid_relevance_reference", "Relevance Gate returned a URL outside the candidate pages or duplicated one")
                seen.add(url)
                if item.status != "uncertain" and not _evidence_occurs(item.evidence, by_url[url]):
                    raise StructuredModelError("invalid_relevance_evidence", "Relevance decision evidence is not present in the candidate page")
                decisions.append(item.model_copy(update={"url": url}))
            if seen != set(by_url):
                raise StructuredModelError("invalid_relevance_reference", "Relevance Gate omitted candidate pages")
        decisions.sort(key=lambda item: canonicalize_url(item.url))
        return decisions


def _identity_context(result: SearchResult) -> dict:
    return {"title": result.title, "url": canonicalize_url(result.url), "snippet": result.content[:1200], "raw_content": (result.raw_content or "")[:12000]}


def _validate_urls(urls: list[str], by_url: dict[str, SearchResult], *, required: bool) -> None:
    normalized = [canonicalize_url(url) for url in urls]
    if required and not normalized:
        raise StructuredModelError("invalid_identity_claim", "Resolved identity requires evidence URLs")
    if len(normalized) != len(set(normalized)) or not set(normalized).issubset(by_url):
        raise StructuredModelError("invalid_identity_reference", "Identity evidence URL is not among the search results")


def _validate_candidate(candidate: IdentityCandidate, by_url: dict[str, SearchResult]) -> None:
    _validate_urls(candidate.evidence_urls, by_url, required=True)
    _require_claim_evidence(candidate.canonical_name, candidate.evidence_urls, by_url)
    if candidate.official_website:
        _validate_website(candidate.official_website, candidate.evidence_urls, by_url)
    if candidate.unified_social_credit_code:
        value = candidate.unified_social_credit_code.upper()
        if not re.fullmatch(r"[0-9A-Z]{18}", value):
            raise StructuredModelError("invalid_identity_claim", "Identity candidate has malformed unified social credit code")
        _require_claim_evidence(value, candidate.evidence_urls, by_url, casefold=False)


def _require_claim_evidence(claim: str, urls: list[str], by_url: dict[str, SearchResult], *, casefold: bool = True) -> None:
    corpus = " ".join(" ".join([by_url[canonicalize_url(url)].title, by_url[canonicalize_url(url)].content, by_url[canonicalize_url(url)].raw_content or ""]) for url in urls)
    if _normalize(claim, casefold=casefold) not in _normalize(corpus, casefold=casefold):
        raise StructuredModelError("unsupported_identity_claim", "Identity claim does not appear in its cited search result content")


def _validate_website(website: str, evidence_urls: list[str], by_url: dict[str, SearchResult]) -> None:
    try:
        canonical = canonicalize_url(website)
        host = urlsplit(canonical).hostname or ""
    except ValueError as exc:
        raise StructuredModelError("invalid_identity_claim", "Official website is not a valid public HTTP(S) URL") from exc
    hosts = {(urlsplit(canonicalize_url(url)).hostname or "").lower() for url in evidence_urls}
    explicit = any(canonical in _normalize(" ".join([by_url[canonicalize_url(url)].title, by_url[canonicalize_url(url)].content, by_url[canonicalize_url(url)].raw_content or ""])) for url in evidence_urls)
    if host.lower() not in hosts and not explicit:
        raise StructuredModelError("unsupported_identity_claim", "Official website host does not come from the supplied search results")


def _evidence_occurs(evidence: str, result: SearchResult) -> bool:
    if not evidence.strip():
        return False
    corpus = " ".join([result.title, result.content, result.raw_content or ""])
    return _normalize(evidence) in _normalize(corpus)


def _is_official_host(url: str, official_website: str | None) -> bool:
    if not official_website:
        return False
    try:
        url_host = (urlsplit(canonicalize_url(url)).hostname or "").lower()
        official_host = (urlsplit(canonicalize_url(official_website)).hostname or "").lower()
    except ValueError:
        return False
    return bool(official_host and (url_host == official_host or url_host.endswith("." + official_host)))


def _normalize(value: str, *, casefold: bool = True) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    return normalized.casefold() if casefold else normalized
