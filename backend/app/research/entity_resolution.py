"""Evidence-bound company identity resolution and page-to-company relevance gating."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class EntityResolutionDraft(StrictModel):
    """LLM-authored identity claims; caller-owned input is deliberately absent."""

    status: Literal["resolved", "ambiguous", "unresolved"]
    canonical_name: str | None = Field(default=None, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    official_website: str | None = None
    unified_social_credit_code: str | None = None
    evidence_urls: list[str] = Field(min_length=1, max_length=20)
    identity_candidates: list[IdentityCandidate] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def status_fields_are_consistent(self) -> "EntityResolutionDraft":
        claims = bool(
            self.canonical_name or self.aliases or self.official_website
            or self.unified_social_credit_code
        )
        if self.status == "resolved" and not self.canonical_name:
            raise ValueError("resolved status requires canonical_name")
        if self.status == "ambiguous" and len(self.identity_candidates) < 2:
            raise ValueError("ambiguous status requires at least two identity_candidates")
        if self.status == "unresolved" and (claims or self.identity_candidates):
            raise ValueError("unresolved status cannot contain identity claims or candidates")
        return self


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
    official_website_resolution_source: Literal["model", "self_attested_page"] | None = None
    official_website_evidence_url: str | None = None
    official_website_diagnostic: str | None = None


class IdentityResolutionTrace(StrictModel):
    queries: list[str] = Field(default_factory=list)
    results_count: int = 0
    status: Literal["resolved", "ambiguous", "unresolved"]
    canonical_name: str | None = None
    official_website: str | None = None
    evidence_count: int = 0
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    reason: str
    official_website_resolution_source: Literal["model", "self_attested_page"] | None = None
    official_website_evidence_url: str | None = None
    official_website_diagnostic: str | None = None


class SourceRelevanceDecision(StrictModel):
    status: Literal["relevant", "irrelevant", "uncertain"]
    url: str = Field(min_length=1, max_length=4096)
    evidence: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=800)
    decision_source: Literal[
        "official_host", "identity_evidence", "model", "model_downgraded", "fallback"
    ] = "model"
    downgrade_reason: str | None = None


class SourceRelevanceDraft(StrictModel):
    page_id: str = Field(min_length=1, max_length=16)
    status: Literal["relevant", "irrelevant", "uncertain"]
    evidence_quote: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=800)


class RelevanceOutput(StrictModel):
    decisions: list[SourceRelevanceDraft] = Field(max_length=100)


class IdentityResolver:
    def __init__(self, model: StructuredJSONModel, prompt_path: str | Path | None = None):
        self.model = model
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "entity_resolver_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")

    async def resolve(self, input_name: str, results: list[SearchResult]) -> EntityResolutionResult:
        if not results:
            return EntityResolutionResult(status="unresolved", input_name=input_name, reason="身份检索没有返回可核验结果。")
        contexts = [_identity_context(item) for item in results]
        draft = await complete_contract(
            self.model,
            self.prompt,
            {"input_name": input_name, "identity_search_results": contexts},
            EntityResolutionDraft,
            stage="entity resolver",
        )
        by_url = {canonicalize_url(x.url): x for x in results}
        website = None
        website_source = None
        website_evidence_url = None
        website_diagnostic = None
        if draft.status == "resolved":
            if not draft.canonical_name:
                raise _identity_error(
                    "invalid_identity_claim", "canonical_name",
                    "resolved_missing_canonical_name",
                    "Resolved identity requires a canonical name supported by evidence.",
                )
            _validate_urls(draft.evidence_urls, by_url, required=True)
            _require_claim_evidence(
                draft.canonical_name, draft.evidence_urls, by_url,
                field="canonical_name", code="unsupported_canonical_name",
            )
            for alias in draft.aliases:
                _require_claim_evidence(
                    alias, draft.evidence_urls, by_url,
                    field="alias", code="unsupported_alias",
                )
            if draft.official_website:
                try:
                    _validate_website(draft.official_website, draft.evidence_urls, by_url)
                except StructuredModelError as exc:
                    website_diagnostic = str(exc.category)
                else:
                    website = canonicalize_url(draft.official_website)
                    website_source = "model"
                    website_evidence_url = next(
                        (url for url in draft.evidence_urls if _website_supported_by_result(website, by_url[canonicalize_url(url)])),
                        draft.evidence_urls[0],
                    )
            if website is None:
                candidates = _self_attested_websites(draft.canonical_name, results)
                candidate_hosts = {_site_host(urlsplit(item[0]).hostname or "") for item in candidates}
                if len(candidate_hosts) == 1 and candidates:
                    website, website_evidence_url = candidates[0]
                    website_source = "self_attested_page"
                    website_diagnostic = None
                elif len(candidate_hosts) > 1:
                    website_diagnostic = "official_website_candidate_conflict"
            if draft.unified_social_credit_code:
                digits = re.sub(r"\s", "", draft.unified_social_credit_code).upper()
                if not re.fullmatch(r"[0-9A-Z]{18}", digits):
                    raise _identity_error(
                        "invalid_identity_claim", "unified_social_credit_code",
                        "malformed_uscc", "Unified social credit code must contain 18 alphanumeric characters.",
                    )
                _require_claim_evidence(
                    digits, draft.evidence_urls, by_url,
                    casefold=False, field="unified_social_credit_code",
                    code="unsupported_uscc",
                )
        elif draft.status == "ambiguous":
            _validate_urls(draft.evidence_urls, by_url, required=True)
            if len(draft.identity_candidates) < 2:
                raise _identity_error(
                    "invalid_identity_claim", "identity_candidates",
                    "ambiguous_insufficient_candidates",
                    "Ambiguous identity requires at least two evidence-bound candidates.",
                )
            for index, candidate in enumerate(draft.identity_candidates):
                _validate_candidate(candidate, by_url, index=index)
        else:
            _validate_urls(draft.evidence_urls, by_url, required=True)
            if (
                draft.canonical_name or draft.aliases or draft.official_website
                or draft.unified_social_credit_code
            ):
                field = (
                    "canonical_name" if draft.canonical_name else
                    "aliases" if draft.aliases else
                    "official_website" if draft.official_website else
                    "unified_social_credit_code"
                )
                raise _identity_error(
                    "invalid_identity_claim", field,
                    "unresolved_contains_identity_claim",
                    "Unresolved identity cannot include asserted identity fields.",
                )

        result_data = draft.model_dump()
        result_data["official_website"] = website
        result_data["official_website_resolution_source"] = website_source
        result_data["official_website_evidence_url"] = website_evidence_url
        result_data["official_website_diagnostic"] = website_diagnostic
        return EntityResolutionResult(input_name=input_name, **result_data)


class EnterpriseRelevanceGate:
    """Batch relevance check; only relevant decisions are eligible for ingestion."""

    def __init__(self, model: StructuredJSONModel, prompt_path: str | Path | None = None):
        self.model = model
        path = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[3] / "prompts" / "enterprise_relevance_prompt.md"
        self.prompt = path.read_text(encoding="utf-8")
        self.last_diagnostics: dict[str, object] = {}

    async def assess(
        self,
        company: Company,
        results: list[SearchResult],
        *,
        trusted_identity_urls: list[str] | set[str] | None = None,
    ) -> list[SourceRelevanceDecision]:
        diagnostics: dict[str, object] = {
            "relevance_model_decision_count": 0,
            "relevance_downgrade_count": 0,
            "relevance_missing_decision_count": 0,
            "relevance_duplicate_decision_count": 0,
            "relevance_unknown_reference_count": 0,
            "relevance_gate_fallback_count": 0,
            "identity_evidence_fast_path_count": 0,
            "official_host_fast_path_count": 0,
            "relevance_gate_fallback": False,
            "relevance_gate_error_category": None,
            "warnings": [],
        }
        self.last_diagnostics = diagnostics
        if not results:
            return []
        pending, decisions = [], []
        trusted = {
            canonicalize_url(url) for url in (trusted_identity_urls or [])
        }
        for result in results:
            canonical_url = canonicalize_url(result.url)
            if canonical_url in trusted:
                decisions.append(SourceRelevanceDecision(
                    status="relevant", url=canonical_url, evidence="",
                    reason="Page was already validated as evidence for resolved company identity.",
                    decision_source="identity_evidence",
                ))
                diagnostics["identity_evidence_fast_path_count"] += 1
            elif _is_official_host(result.url, company.official_website):
                decisions.append(SourceRelevanceDecision(
                    status="relevant", url=canonical_url, evidence="",
                    reason="来源 host 与已解析的企业官网 host 一致或为其子域名。",
                    decision_source="official_host",
                ))
                diagnostics["official_host_fast_path_count"] += 1
            else:
                pending.append(result)
        if pending:
            page_map = {f"P{index}": result for index, result in enumerate(pending, start=1)}
            payload = {
                "resolved_company": {"canonical_name": company.canonical_name, "aliases": company.aliases, "official_website": company.official_website, "unified_social_credit_code": company.unified_social_credit_code},
                "pages": [
                    {"page_id": page_id, **_identity_context(result)}
                    for page_id, result in page_map.items()
                ],
            }
            try:
                output = await complete_contract(
                    self.model, self.prompt, payload, RelevanceOutput,
                    stage="enterprise relevance gate",
                )
            except Exception as exc:
                diagnostics["relevance_gate_fallback"] = True
                diagnostics["relevance_gate_fallback_count"] = 1
                diagnostics["relevance_gate_error_category"] = str(
                    getattr(exc, "category", type(exc).__name__)
                )
                diagnostics["warnings"].append("relevance_gate_fallback")
                decisions.extend(
                    SourceRelevanceDecision(
                        status="uncertain", url=canonicalize_url(result.url),
                        evidence="",
                        reason="relevance model call failed; page relevance could not be verified",
                        decision_source="fallback",
                        downgrade_reason="relevance_gate_fallback",
                    )
                    for result in pending
                )
            else:
                by_id = {page_id: result for page_id, result in page_map.items()}
                grouped: dict[str, list[SourceRelevanceDraft]] = {}
                for item in output.decisions:
                    if item.page_id not in by_id:
                        diagnostics["relevance_unknown_reference_count"] += 1
                        diagnostics["warnings"].append("unknown_page_reference")
                        continue
                    grouped.setdefault(item.page_id, []).append(item)

                for page_id, result in by_id.items():
                    candidates = grouped.get(page_id, [])
                    url = canonicalize_url(result.url)
                    if len(candidates) > 1:
                        diagnostics["relevance_duplicate_decision_count"] += 1
                        diagnostics["relevance_downgrade_count"] += 1
                        decisions.append(SourceRelevanceDecision(
                            status="uncertain", url=url, evidence="",
                            reason="model returned duplicate page decisions",
                            decision_source="model_downgraded",
                            downgrade_reason="duplicate_model_decision",
                        ))
                        continue
                    if not candidates:
                        diagnostics["relevance_missing_decision_count"] += 1
                        diagnostics["relevance_downgrade_count"] += 1
                        decisions.append(SourceRelevanceDecision(
                            status="uncertain", url=url, evidence="",
                            reason="model omitted candidate page",
                            decision_source="model_downgraded",
                            downgrade_reason="missing_model_decision",
                        ))
                        continue
                    item = candidates[0]
                    diagnostics["relevance_model_decision_count"] += 1
                    if item.status == "uncertain":
                        decisions.append(SourceRelevanceDecision(
                            status="uncertain", url=url, evidence="",
                            reason=item.reason, decision_source="model",
                        ))
                    elif not _evidence_occurs(item.evidence_quote, result):
                        diagnostics["relevance_downgrade_count"] += 1
                        decisions.append(SourceRelevanceDecision(
                            status="uncertain", url=url, evidence="",
                            reason="model evidence quote could not be verified against candidate page",
                            decision_source="model_downgraded",
                            downgrade_reason="evidence_quote_not_verified",
                        ))
                    else:
                        decisions.append(SourceRelevanceDecision(
                            status=item.status, url=url,
                            evidence=item.evidence_quote, reason=item.reason,
                            decision_source="model",
                        ))
        decisions.sort(key=lambda item: canonicalize_url(item.url))
        return decisions


def _identity_context(result: SearchResult) -> dict:
    return {"title": result.title, "url": canonicalize_url(result.url), "snippet": result.content[:1200], "raw_content": (result.raw_content or "")[:12000]}


def _identity_error(category: str, field: str, code: str, message: str) -> StructuredModelError:
    return StructuredModelError(
        category,
        "Entity identity validation failed",
        diagnostics=[{
            "attempt": "deterministic_validation",
            "location": field,
            "type": code,
            "message": message,
        }],
    )


def _validate_urls(
    urls: list[str], by_url: dict[str, SearchResult], *, required: bool,
    field: str = "evidence_urls",
) -> None:
    try:
        normalized = [canonicalize_url(url) for url in urls]
    except (TypeError, ValueError) as exc:
        raise _identity_error(
            "invalid_identity_claim", field, "identity_evidence_url_not_in_results",
            "Identity evidence URL must match one of the supplied search results.",
        ) from exc
    if required and not normalized:
        raise _identity_error(
            "invalid_identity_claim", field, "resolved_missing_evidence",
            "Resolved identity requires at least one supplied identity evidence URL.",
        )
    if len(normalized) != len(set(normalized)) or not set(normalized).issubset(by_url):
        raise _identity_error(
            "invalid_identity_claim", field, "identity_evidence_url_not_in_results",
            "Identity evidence URL must match one of the supplied search results.",
        )


def _validate_candidate(
    candidate: IdentityCandidate, by_url: dict[str, SearchResult], *, index: int = 0
) -> None:
    prefix = f"identity_candidates.{index}"
    _validate_urls(
        candidate.evidence_urls, by_url, required=True, field=f"{prefix}.evidence_urls"
    )
    _require_claim_evidence(
        candidate.canonical_name, candidate.evidence_urls, by_url,
        field=f"{prefix}.canonical_name", code="unsupported_canonical_name",
    )
    if candidate.official_website:
        _validate_website(
            candidate.official_website, candidate.evidence_urls, by_url,
            field=f"{prefix}.official_website",
        )
    if candidate.unified_social_credit_code:
        value = candidate.unified_social_credit_code.upper()
        if not re.fullmatch(r"[0-9A-Z]{18}", value):
            raise _identity_error(
                "invalid_identity_claim", f"{prefix}.unified_social_credit_code",
                "malformed_uscc", "Unified social credit code must contain 18 alphanumeric characters.",
            )
        _require_claim_evidence(
            value, candidate.evidence_urls, by_url, casefold=False,
            field=f"{prefix}.unified_social_credit_code", code="unsupported_uscc",
        )


def _require_claim_evidence(
    claim: str, urls: list[str], by_url: dict[str, SearchResult], *,
    casefold: bool = True, field: str = "canonical_name",
    code: str = "unsupported_identity_claim",
) -> None:
    corpus = " ".join(" ".join([by_url[canonicalize_url(url)].title, by_url[canonicalize_url(url)].content, by_url[canonicalize_url(url)].raw_content or ""]) for url in urls)
    if not claim.strip() or _normalize(claim, casefold=casefold) not in _normalize(corpus, casefold=casefold):
        raise _identity_error(
            "unsupported_identity_claim", field, code,
            "Identity claim is not present in the cited title, snippet, or page content.",
        )


def _validate_website(
    website: str, evidence_urls: list[str], by_url: dict[str, SearchResult], *,
    field: str = "official_website",
) -> None:
    try:
        canonical = canonicalize_url(website)
        host = urlsplit(canonical).hostname or ""
    except ValueError as exc:
        raise _identity_error(
            "invalid_identity_claim", field, "malformed_official_website",
            "Official website must be a valid public HTTP(S) URL.",
        ) from exc
    hosts = {_site_host(urlsplit(canonicalize_url(url)).hostname or "") for url in evidence_urls}
    website_forms = {canonical, website.strip()}
    if canonical.endswith("/"):
        website_forms.add(canonical.rstrip("/"))
    else:
        website_forms.add(canonical + "/")
    explicit = any(
        _normalize(form) in _normalize(
            " ".join([
                by_url[canonicalize_url(url)].title,
                by_url[canonicalize_url(url)].content,
                by_url[canonicalize_url(url)].raw_content or "",
            ])
        )
        for form in website_forms
        for url in evidence_urls
    )
    if _site_host(host) not in hosts and not explicit:
        raise _identity_error(
            "unsupported_identity_claim", field, "unsupported_official_website",
            "Official website is not supported by supplied identity evidence.",
        )


def _website_supported_by_result(website: str, result: SearchResult) -> bool:
    try:
        website_host = _site_host(urlsplit(canonicalize_url(website)).hostname or "")
        page_host = _site_host(urlsplit(canonicalize_url(result.url)).hostname or "")
    except ValueError:
        return False
    corpus = " ".join([result.title, result.content, result.raw_content or ""])
    explicit_url = any(
        _site_host(urlsplit(canonicalize_url(match.group(0).rstrip(".,;:!?)，。；：！》】"))).hostname or "") == website_host
        for match in re.finditer(r"https?://[^\s<>\"'，。；：！）》】]+", corpus, flags=re.IGNORECASE)
        if _valid_public_url(match.group(0).rstrip(".,;:!?)，。；：！》】"))
    )
    return bool(website_host and (website_host == page_host or explicit_url))


def _self_attested_websites(
    canonical_name: str, results: list[SearchResult]
) -> list[tuple[str, str]]:
    marker_pattern = re.compile(
        r"(?:官方网站|公司官网|企业官网|官网|official\s+(?:website|site))",
        flags=re.IGNORECASE,
    )
    candidates: dict[str, tuple[str, str]] = {}
    normalized_name = _normalize(canonical_name)
    for result in results:
        corpus = " ".join([result.title, result.content, result.raw_content or ""])
        if normalized_name not in _normalize(corpus) or not marker_pattern.search(corpus):
            continue
        try:
            page_host = _site_host(urlsplit(canonicalize_url(result.url)).hostname or "")
        except ValueError:
            continue
        for match in re.finditer(r"https?://[^\s<>\"'，。；：！）》】]+", corpus, flags=re.IGNORECASE):
            raw_url = match.group(0).rstrip(".,;:!?)，。；：！》】")
            if not _valid_public_url(raw_url):
                continue
            try:
                parsed = urlsplit(canonicalize_url(raw_url))
                candidate_host = _site_host(parsed.hostname or "")
            except ValueError:
                continue
            # Self-attestation is accepted only when the page explicitly names its own host.
            if not page_host or candidate_host != page_host:
                continue
            host = urlsplit(canonicalize_url(result.url)).hostname or ""
            website = f"https://{host}/"
            candidates.setdefault(page_host, (website, canonicalize_url(result.url)))
    return list(candidates.values())


def _valid_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(canonicalize_url(url))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def _evidence_occurs(evidence: str, result: SearchResult) -> bool:
    if not evidence.strip():
        return False
    corpus = " ".join([result.title, result.content, result.raw_content or ""])
    return _normalize(evidence) in _normalize(corpus)


def _is_official_host(url: str, official_website: str | None) -> bool:
    if not official_website:
        return False
    try:
        url_host = _site_host(urlsplit(canonicalize_url(url)).hostname or "")
        official_host = _site_host(urlsplit(canonicalize_url(official_website)).hostname or "")
    except ValueError:
        return False
    return bool(official_host and (url_host == official_host or url_host.endswith("." + official_host)))


def _site_host(host: str) -> str:
    normalized = host.lower()
    return normalized[4:] if normalized.startswith("www.") else normalized


def _normalize(value: str, *, casefold: bool = True) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    return normalized.casefold() if casefold else normalized
