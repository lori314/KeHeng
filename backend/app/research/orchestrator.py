"""Bounded iterative company research over a provider-neutral search API."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

from pydantic import ValidationError

from app.knowledge.contracts import Company, CompanyResolutionStatus
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase
from app.research.contracts import (
    InitialRetrievalPlan,
    IterativeResearchResult,
    ResearchTraceEntry,
    RetrievalPlanningContext,
    RetrievalQuery,
    RetrievedSourceSummary,
    SearchRequest,
    SearchResult,
)
from app.research.ingestion import (
    FULL_CONTENT_MIN_CHARS,
    WebContentChunker,
    prepare_web_source,
)
from app.research.planner import RetrievalPlanner
from app.research.search_provider import SearchProviderError, WebSearchProvider
from app.knowledge.identity import canonicalize_url, company_for_name
from app.research.entity_resolution import (
    EnterpriseRelevanceGate,
    EntityResolutionResult,
    IdentityResolver,
    IdentityResolutionTrace,
)
from app.research.source_classifier import SourceTypeClassifier


class IterativeResearchService:
    def __init__(
        self,
        planner: RetrievalPlanner,
        search_provider: WebSearchProvider,
        knowledge_base: SharedKnowledgeBase,
        *,
        max_source_count: int = 25,
        no_gain_round_limit: int = 1,
        min_extract_chars: int = FULL_CONTENT_MIN_CHARS,
        chunker: WebContentChunker | None = None,
        entity_resolver: IdentityResolver | None = None,
        relevance_gate: EnterpriseRelevanceGate | None = None,
        source_classifier: SourceTypeClassifier | None = None,
    ) -> None:
        if max_source_count < 1 or no_gain_round_limit < 1:
            raise ValueError("source and no-gain limits must be positive")
        self.planner = planner
        self.search_provider = search_provider
        self.knowledge_base = knowledge_base
        self.max_source_count = max_source_count
        self.no_gain_round_limit = no_gain_round_limit
        self.min_extract_chars = min_extract_chars
        self.chunker = chunker or WebContentChunker()
        model = getattr(planner, "model", None)
        self.entity_resolver = entity_resolver or (IdentityResolver(model) if model is not None else None)
        self.relevance_gate = relevance_gate or (EnterpriseRelevanceGate(model) if model is not None else None)
        self.source_classifier = source_classifier or SourceTypeClassifier()
        self.last_execution_trace: dict[str, object] = {}

    async def run(
        self,
        enterprise_name: str,
        *,
        max_rounds: int = 3,
        max_queries_per_round: int = 6,
        max_results_per_query: int = 5,
        max_sources: int | None = None,
    ) -> IterativeResearchResult:
        enterprise_name = " ".join(unicodedata.normalize("NFKC", enterprise_name).split())
        if not enterprise_name:
            raise ValueError("enterprise_name must not be empty")
        if not 1 <= max_rounds <= 10:
            raise ValueError("max_rounds must be between 1 and 10")
        if not 1 <= max_queries_per_round <= 8:
            raise ValueError("max_queries_per_round must be between 1 and 8")
        if not 1 <= max_results_per_query <= 20:
            raise ValueError("max_results_per_query must be between 1 and 20")
        requested_source_limit = self.max_source_count if max_sources is None else max_sources
        source_limit = min(requested_source_limit, self.max_source_count)
        if source_limit < 1:
            raise ValueError("max_sources must be positive")

        self.last_execution_trace = {
            "enterprise_name": enterprise_name,
            "queries_executed": [],
            "identity_queries": [],
            "identity_results_count": 0,
            "sources_found": 0,
            "sources_ingested": 0,
            "rounds_completed": 0,
            "current_stage": "planning",
            "search_provider": str(getattr(self.search_provider, "name", "unknown")),
        }

        initial_plan = await self.planner.plan_initial(enterprise_name)
        company = company_for_name(enterprise_name)
        stored_company = self.knowledge_base.repository.get_company(company.company_id or "")
        if stored_company is not None:
            company = stored_company
        if self.entity_resolver is None or self.relevance_gate is None:
            raise RuntimeError("Entity resolution and enterprise relevance components are required")
        self._update_execution_trace(current_stage="planned")
        identity_queries = list(initial_plan.company_identity_queries[: min(4, max_queries_per_round)])
        if not identity_queries:
            identity_queries = [RetrievalQuery(query=f"{enterprise_name} 企业主体 官网 工商", category="company_identity")]
        identity_queries = _dedupe_queries(identity_queries, set(), enterprise_name=enterprise_name, known_terms=[], initial=True, max_queries=min(4, max_queries_per_round))
        if not identity_queries:
            return self._empty_result(enterprise_name, stop_reason="entity_unresolved", status="unresolved")
        executed_queries: list[str] = []
        executed_keys: set[str] = set()
        seen_urls: set[str] = set()
        seen_results: dict[str, SearchResult] = {}
        discovered_terms: list[str] = []
        gaps = _dedupe_text(initial_plan.information_gaps)
        traces: list[ResearchTraceEntry] = []
        warnings: list[str] = []
        sources_found = 0
        sources_ingested = 0
        new_versions = 0
        duplicate_versions = 0
        chunks_added = 0
        no_gain_rounds = 0
        identity_query_strings = [item.query for item in identity_queries]
        executed_queries.extend(identity_query_strings)
        executed_keys.update(_query_key(query) for query in identity_query_strings)
        self._update_execution_trace(
            current_stage="planned",
            identity_queries=identity_query_strings,
            queries_executed=list(executed_queries),
        )
        self._update_execution_trace(current_stage="identity_search_started")
        raw_identity = await self._search_round(identity_queries, max_results_per_query=min(5, max_results_per_query))
        identity_results, identity_duplicates, identity_new_count = _unique_results(raw_identity, seen_urls, seen_results, max(0, source_limit - sources_found))
        sources_found += identity_new_count
        self._update_execution_trace(
            current_stage="identity_search_completed",
            identity_results_count=len(identity_results),
            sources_found=sources_found,
        )
        identity_failures: list[str] = []
        await self._hydrate_content(identity_results, identity_failures)
        self._update_execution_trace(current_stage="entity_resolution_started")
        try:
            identity = await self.entity_resolver.resolve(enterprise_name, identity_results)
        except Exception as exc:
            category = getattr(exc, "category", None)
            failure_stage = (
                "entity_validation_failed"
                if category in {"invalid_identity_claim", "invalid_identity_reference", "unsupported_identity_claim"}
                else "entity_resolution_failed"
            )
            self._update_execution_trace(current_stage=failure_stage)
            raise
        self._update_execution_trace(current_stage="entity_resolution_completed")
        identity_trace = IdentityResolutionTrace(queries=identity_query_strings, results_count=len(identity_results), status=identity.status, canonical_name=identity.canonical_name, official_website=identity.official_website, evidence_count=len(identity.evidence_urls), candidates=identity.identity_candidates, reason=identity.reason, official_website_resolution_source=identity.official_website_resolution_source, official_website_evidence_url=identity.official_website_evidence_url, official_website_diagnostic=identity.official_website_diagnostic)
        if identity.status != "resolved":
            stop_reason = "entity_ambiguous" if identity.status == "ambiguous" else "entity_unresolved"
            return IterativeResearchResult(enterprise_name=enterprise_name, rounds=1, queries_executed=executed_queries, sources_found=sources_found, sources_ingested=0, new_versions=0, duplicate_versions=0, knowledge_chunks_added=0, trace=[], remaining_information_gaps=gaps, stop_reason=stop_reason, entity_resolution_status=identity.status, identity_evidence_count=len(identity.evidence_urls), identity_candidates=[x.model_dump(mode="json") for x in identity.identity_candidates], identity_trace=identity_trace.model_dump(mode="json"), warnings=identity_failures)

        now = datetime.now(timezone.utc).isoformat()
        metadata = dict(company.metadata)
        metadata.update({"resolution_evidence_urls": identity.evidence_urls, "resolved_at": now, "resolver_version": "entity-resolver.v2", "official_website_resolution_source": identity.official_website_resolution_source, "official_website_evidence_url": identity.official_website_evidence_url, "official_website_diagnostic": identity.official_website_diagnostic})
        company = company.model_copy(update={"canonical_name": identity.canonical_name, "aliases": identity.aliases, "official_website": identity.official_website, "unified_social_credit_code": identity.unified_social_credit_code, "resolution_status": CompanyResolutionStatus.RESOLVED, "metadata": metadata})
        self.knowledge_base.repository.upsert_company(company)

        source_counts: Counter[str] = Counter()
        ingested_urls: set[str] = set()
        aggregate = {"ingested": 0, "new_versions": 0, "duplicate_versions": 0, "chunks": 0, "relevant": 0, "irrelevant": 0, "uncertain": 0}
        self._update_execution_trace(current_stage="identity_relevance_gate_started")
        identity_ingested, identity_type_counts, identity_counts, identity_summaries, identity_relevance_diagnostics = await self._gate_and_ingest(
            identity_results, company, aggregate, ingested_urls,
            trusted_identity_urls=identity.evidence_urls,
        )
        self._update_execution_trace(
            current_stage="identity_sources_ingested",
            sources_ingested=identity_counts["ingested"],
        )
        warnings.extend(identity_relevance_diagnostics.get("warnings", []))
        source_counts.update(identity_type_counts)
        sources_ingested += identity_counts["ingested"]
        self._update_execution_trace(sources_ingested=sources_ingested)
        new_versions += identity_counts["new_versions"]
        duplicate_versions += identity_counts["duplicate_versions"]
        chunks_added += identity_counts["chunks"]
        relevant_total = identity_counts["relevant"]
        irrelevant_total = identity_counts["irrelevant"]
        uncertain_total = identity_counts["uncertain"]
        discovered_terms: list[str] = []
        identity_entry = ResearchTraceEntry(round=1, queries=identity_query_strings, results_count=len(raw_identity), new_source_count=identity_counts["ingested"], duplicate_source_count=identity_duplicates, new_terms=[], information_gaps=gaps, planner_reason=initial_plan.reason, content_retrieval_failures=identity_failures, relevant_source_count=identity_counts["relevant"], irrelevant_source_count=identity_counts["irrelevant"], uncertain_source_count=identity_counts["uncertain"], source_type_counts=identity_type_counts, relevance_diagnostics=identity_relevance_diagnostics)
        traces.append(identity_entry)
        self._update_execution_trace(rounds_completed=1)
        stop_reason = "max_rounds"
        if sources_found >= source_limit:
            stop_reason = "max_sources"
            identity_entry.stop_reason = stop_reason
            planning = None
            pending = []
        else:
            identity_pending = [item for item in initial_plan.queries() if _query_key(item.query) not in executed_keys]
            pending = _dedupe_queries(identity_pending, executed_keys, enterprise_name=company.canonical_name, identity_names=company.aliases, known_terms=[], initial=True, max_queries=max_queries_per_round)
            planning = None
            if not pending and max_rounds > 1:
                context = RetrievalPlanningContext(enterprise_name=company.canonical_name, aliases=company.aliases, current_round=1, executed_queries=executed_queries, sources=identity_summaries, discovered_terms=[], information_gaps=gaps)
                planning = await self.planner.plan_next(context)
                identity_entry.new_terms = _terms_supported_by_sources(planning.discovered_terms, identity_ingested)
                discovered_terms = identity_entry.new_terms
                gaps = _dedupe_text(planning.information_gaps)
                identity_entry.information_gaps = gaps
                pending = planning.new_queries
        pending_is_initial = planning is None
        for round_number in range(2, max_rounds + 1):
            if sources_found >= source_limit:
                stop_reason = "max_sources"
                traces[-1].stop_reason = stop_reason
                break
            current_queries = _dedupe_queries(pending, executed_keys, enterprise_name=company.canonical_name, identity_names=company.aliases, known_terms=discovered_terms, initial=pending_is_initial, max_queries=max_queries_per_round)
            if not current_queries:
                stop_reason = "no_grounded_followup_queries"
                traces[-1].stop_reason = stop_reason
                break
            query_strings = [item.query for item in current_queries]
            executed_queries.extend(query_strings)
            executed_keys.update(_query_key(query) for query in query_strings)
            pending_is_initial = False
            self._update_execution_trace(
                current_stage="research_search_started",
                queries_executed=list(executed_queries),
                sources_found=sources_found,
            )
            raw_results = await self._search_round(current_queries, max_results_per_query=max_results_per_query)
            unique_results, duplicate_count, new_source_count = _unique_results(raw_results, seen_urls, seen_results, max(0, source_limit - sources_found))
            sources_found += new_source_count
            self._update_execution_trace(
                current_stage="research_search_completed",
                sources_found=sources_found,
            )
            failures: list[str] = []
            await self._hydrate_content(unique_results, failures)
            round_ingested, type_counts, round_counts, round_summaries, relevance_diagnostics = await self._gate_and_ingest(unique_results, company, aggregate, ingested_urls)
            warnings.extend(relevance_diagnostics.get("warnings", []))
            source_counts.update(type_counts)
            sources_ingested += round_counts["ingested"]
            self._update_execution_trace(sources_ingested=sources_ingested)
            new_versions += round_counts["new_versions"]
            duplicate_versions += round_counts["duplicate_versions"]
            chunks_added += round_counts["chunks"]
            relevant_total += round_counts["relevant"]
            irrelevant_total += round_counts["irrelevant"]
            uncertain_total += round_counts["uncertain"]
            followup = None
            if round_number < max_rounds and sources_found < source_limit:
                context = RetrievalPlanningContext(enterprise_name=company.canonical_name, aliases=company.aliases, current_round=round_number, executed_queries=executed_queries, sources=round_summaries, discovered_terms=discovered_terms, information_gaps=gaps)
                followup = await self.planner.plan_next(context)
                new_terms = _terms_supported_by_sources(followup.discovered_terms, round_ingested)
                new_terms = [term for term in new_terms if _text_key(term) not in {_text_key(value) for value in discovered_terms}]
                resolved_gap_count = len({_text_key(item) for item in gaps} - {_text_key(item) for item in followup.information_gaps})
                no_gain_rounds = 0 if round_counts["ingested"] or new_terms or resolved_gap_count else no_gain_rounds + 1
                discovered_terms = _dedupe_text([*discovered_terms, *new_terms])
                gaps = _dedupe_text(followup.information_gaps)
                pending = followup.new_queries
                reason = followup.reason
            else:
                new_terms, reason, pending = [], "Reached a configured hard limit.", []
            entry = ResearchTraceEntry(round=round_number, queries=query_strings, results_count=len(raw_results), new_source_count=round_counts["ingested"], duplicate_source_count=duplicate_count, new_terms=new_terms, information_gaps=gaps, planner_reason=reason, content_retrieval_failures=failures, relevant_source_count=round_counts["relevant"], irrelevant_source_count=round_counts["irrelevant"], uncertain_source_count=round_counts["uncertain"], source_type_counts=type_counts, relevance_diagnostics=relevance_diagnostics)
            traces.append(entry)
            self._update_execution_trace(rounds_completed=round_number)
            if sources_found >= source_limit:
                stop_reason = "max_sources"
            elif followup is not None and not followup.should_continue:
                stop_reason = "planner_stopped"
            elif no_gain_rounds >= self.no_gain_round_limit:
                stop_reason = "no_information_gain"
            elif round_number >= max_rounds:
                stop_reason = "max_rounds"
            elif not pending:
                stop_reason = "no_grounded_followup_queries"
            else:
                continue
            entry.stop_reason = stop_reason
            break

        self._update_execution_trace(current_stage="research_completed")
        return IterativeResearchResult(enterprise_name=enterprise_name, rounds=len(traces), queries_executed=executed_queries, sources_found=sources_found, sources_ingested=sources_ingested, new_versions=new_versions, duplicate_versions=duplicate_versions, knowledge_chunks_added=chunks_added, trace=traces, remaining_information_gaps=gaps, stop_reason=stop_reason, warnings=warnings, entity_resolution_status=identity.status, resolved_canonical_name=company.canonical_name, official_website=company.official_website, identity_evidence_count=len(identity.evidence_urls), identity_candidates=[x.model_dump(mode="json") for x in identity.identity_candidates], identity_trace=identity_trace.model_dump(mode="json"), relevant_source_count=relevant_total, irrelevant_source_count=irrelevant_total, uncertain_source_count=uncertain_total, source_type_counts=dict(source_counts))

    def _update_execution_trace(self, **updates: object) -> None:
        """Keep only non-content execution metadata for failure reporting."""
        self.last_execution_trace.update(updates)

    def _empty_result(self, enterprise_name: str, *, stop_reason: str, status: str) -> IterativeResearchResult:
        return IterativeResearchResult(enterprise_name=enterprise_name, rounds=0, queries_executed=[], sources_found=0, sources_ingested=0, new_versions=0, duplicate_versions=0, knowledge_chunks_added=0, trace=[], remaining_information_gaps=[], stop_reason=stop_reason, entity_resolution_status=status)

    async def _gate_and_ingest(
        self,
        results: list[SearchResult],
        company: Company,
        aggregate: dict,
        ingested_urls: set[str],
        *,
        trusted_identity_urls: list[str] | None = None,
    ):
        self._update_execution_trace(current_stage="relevance_gate_started")
        decisions = await self.relevance_gate.assess(
            company, results, trusted_identity_urls=trusted_identity_urls
        )
        relevance_diagnostics = dict(
            getattr(self.relevance_gate, "last_diagnostics", {}) or {}
        )
        self._update_execution_trace(current_stage="source_ingestion_started")
        result_by_url = {canonicalize_url(item.url): item for item in results}
        relevant, summaries, type_counts = [], [], Counter()
        counts = {"ingested": 0, "new_versions": 0, "duplicate_versions": 0, "chunks": 0, "relevant": 0, "irrelevant": 0, "uncertain": 0}
        for decision in decisions:
            counts[decision.status] += 1
            if decision.status != "relevant":
                continue
            result = result_by_url[canonicalize_url(decision.url)]
            source_type = self.source_classifier.classify(result, company).value
            type_counts[source_type] += 1
            if not (result.raw_content or result.content).strip():
                continue
            prepared = prepare_web_source(result, enterprise_name=company.canonical_name, company=company, retrieved_at=datetime.now(timezone.utc), chunker=self.chunker, source_classifier=self.source_classifier)
            preexisting = {chunk.chunk_id for chunk in prepared.chunks if self.knowledge_base.repository.get_chunk(chunk.chunk_id) is not None}
            outcome = await self.knowledge_base.upsert_source_version(prepared.source, prepared.source_version, prepared.chunks, company=company)
            if canonicalize_url(result.url) not in ingested_urls:
                counts["ingested"] += 1
                ingested_urls.add(canonicalize_url(result.url))
            counts["new_versions"] += int(outcome.created_new_version)
            counts["duplicate_versions"] += int(not outcome.created_new_version)
            counts["chunks"] += sum(chunk.chunk_id not in preexisting for chunk in prepared.chunks)
            relevant.append(result)
            summaries.append(RetrievedSourceSummary(title=result.title, url=result.url, snippet=result.content[:700], content_scope=prepared.content_scope))
        aggregate.update({key: aggregate.get(key, 0) + value for key, value in counts.items()})
        self._update_execution_trace(current_stage="source_ingestion_completed")
        return relevant, dict(type_counts), counts, summaries, relevance_diagnostics

    async def _search_round(
        self, queries: list[RetrievalQuery], *, max_results_per_query: int
    ) -> list[SearchResult]:
        semaphore = asyncio.Semaphore(min(8, len(queries)))

        async def execute(item: RetrievalQuery) -> list[SearchResult]:
            async with semaphore:
                request = SearchRequest(
                    query=item.query,
                    max_results=max_results_per_query,
                    topic="news" if "news" in item.category.casefold() else None,
                )
                return (await self.search_provider.search(request))[:max_results_per_query]

        try:
            batches = await asyncio.gather(*(execute(item) for item in queries))
        except SearchProviderError:
            raise
        return [result for batch in batches for result in batch]

    async def _hydrate_content(
        self, results: list[SearchResult], failures: list[str]
    ) -> None:
        urls = [
            result.url
            for result in results
            if not result.raw_content
            or len(result.raw_content.strip()) < self.min_extract_chars
        ]
        if not urls:
            return
        extracted: dict[str, str] = {}
        for index in range(0, len(urls), 20):
            batch = urls[index : index + 20]
            try:
                extracted.update(await self.search_provider.extract(batch))
            except SearchProviderError as exc:
                failures.append(exc.category)
                extracted.update(exc.partial_results)
        for result in results:
            content = extracted.get(result.url)
            if content and content.strip():
                result.raw_content = content


def _initial_queries(
    plan: InitialRetrievalPlan, enterprise_name: str, max_queries: int
) -> list[RetrievalQuery]:
    return _dedupe_queries(
        plan.queries(), set(), enterprise_name=enterprise_name, known_terms=[],
        initial=True, max_queries=max_queries,
    )


def _unique_results(results: list[SearchResult], seen_urls: set[str], seen_results: dict[str, SearchResult], limit: int) -> tuple[list[SearchResult], int, int]:
    unique: list[SearchResult] = []
    positions: dict[str, int] = {}
    duplicates = 0
    new_sources = 0
    for result in results:
        canonical = canonicalize_url(result.url)
        if canonical in seen_urls:
            duplicates += 1
            existing = seen_results.get(canonical)
            if existing is not None and len(result.raw_content or "") > len(existing.raw_content or ""):
                enriched = result.model_copy(update={"url": canonical})
                seen_results[canonical] = enriched
                position = positions.get(canonical)
                if position is not None:
                    unique[position] = enriched
                else:
                    unique.append(enriched)
            continue
        if new_sources >= limit:
            continue
        seen_urls.add(canonical)
        seen_results[canonical] = result.model_copy(update={"url": canonical})
        new_sources += 1
        positions[canonical] = len(unique)
        unique.append(seen_results[canonical])
    return unique, duplicates, new_sources


def _dedupe_queries(
    queries: list[RetrievalQuery],
    already_executed: set[str],
    *,
    enterprise_name: str,
    identity_names: list[str] | None = None,
    known_terms: list[str],
    initial: bool,
    max_queries: int,
) -> list[RetrievalQuery]:
    output: list[RetrievalQuery] = []
    grounded_names = [enterprise_name, *(identity_names or [])]
    normalized_names = [_text_key(name) for name in grounded_names if _text_key(name)]
    local_keys: set[str] = set()
    for item in queries:
        query = " ".join(unicodedata.normalize("NFKC", item.query).split())
        if not query:
            continue
        key = _query_key(query)
        if key in already_executed or key in local_keys:
            continue
        if initial:
            if not any(name in _text_key(query) for name in normalized_names):
                query = f"{enterprise_name} {query}".strip()
            key = _query_key(query)
            if key in already_executed or key in local_keys:
                continue
        else:
            matched_term = next(
                (term for term in known_terms if _text_key(term) in _text_key(query)), None
            )
            if matched_term is None:
                continue
            if not any(name in _text_key(query) for name in normalized_names):
                query = f"{enterprise_name} {query}".strip()
                key = _query_key(query)
            if key in already_executed or key in local_keys:
                continue
        try:
            normalized_query = RetrievalQuery(query=query, category=item.category)
        except ValidationError:
            continue
        output.append(normalized_query)
        local_keys.add(key)
        if len(output) >= max_queries:
            break
    return output


def _terms_supported_by_sources(terms: list[str], sources: list[SearchResult]) -> list[str]:
    corpus = " ".join(
        f"{source.title} {source.content} {source.raw_content or ''}" for source in sources
    )
    corpus_key = _text_key(corpus)
    return [
        term.strip()
        for term in _dedupe_text(terms)
        if len(term.strip()) >= 2 and _text_key(term) in corpus_key
    ]


def _dedupe_text(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        key = _text_key(normalized)
        if normalized and key not in seen:
            output.append(normalized)
            seen.add(key)
    return output


def _query_key(value: str) -> str:
    return _text_key(value)


def _text_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()
