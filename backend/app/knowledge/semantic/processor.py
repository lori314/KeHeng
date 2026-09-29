"""End-to-end raw GENERAL → adaptive technology semantics → derived knowledge."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.llm import StructuredJSONModel
from app.knowledge.contracts import (
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceVersion,
)
from app.knowledge.identity import knowledge_chunk_id_for
from app.knowledge.semantic.classifier import TechnologyDomainClassifier
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.semantic.extractor import TechnologyFactExtractor, source_quality
from app.knowledge.semantic.interpreter import TechnologyInterpreter
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase

PROCESSOR_VERSION = "technology-semantic.v1"
MAX_GENERAL_CHUNKS_PER_PROCESS = 40


class TechnologyKnowledgeProcessor:
    """Process one company's current GENERAL evidence with three separate LLM steps."""

    def __init__(
        self,
        model: StructuredJSONModel,
        knowledge_base: SharedKnowledgeBase,
        *,
        registry: KnowledgeSemanticRegistry | None = None,
        processor_version: str = PROCESSOR_VERSION,
    ) -> None:
        self.model = model
        self.knowledge_base = knowledge_base
        self.repository = knowledge_base.repository
        self.registry = registry or KnowledgeSemanticRegistry()
        self.processor_version = processor_version
        self.classifier = TechnologyDomainClassifier(model, self.registry)
        self.extractor = TechnologyFactExtractor(
            model,
            self.registry,
            processor_version=processor_version,
        )
        self.interpreter = TechnologyInterpreter(model, self.registry)

    async def process_company(self, company_id: str) -> TechnologySemanticProfile:
        company = self.repository.get_company(company_id)
        if company is None:
            raise ValueError(f"Unknown company_id: {company_id}")
        all_chunks = self.repository.list_current_chunks(company_id, KnowledgeLayer.GENERAL.value)
        chunks = all_chunks[:MAX_GENERAL_CHUNKS_PER_PROCESS]
        sources: dict[str, Source] = {}
        versions: dict[str, SourceVersion] = {}
        quality_by_chunk: dict[str, dict[str, str]] = {}
        for chunk in chunks:
            source = self.repository.get_source(chunk.source_id)
            version = (
                self.repository.get_source_version(chunk.source_version_id)
                if chunk.source_version_id
                else None
            )
            if source is None or version is None:
                raise RuntimeError(f"KnowledgeChunk {chunk.chunk_id} has missing provenance")
            sources[source.source_id] = source
            versions[version.source_version_id] = version
            quality = source_quality(source, chunk, version)
            quality_by_chunk[chunk.chunk_id] = {
                "category": quality.category,
                "source_type": quality.source_type,
                "content_scope": quality.content_scope,
                "rationale": quality.rationale,
            }

        domain_profile, selection, classify_gaps = await self.classifier.classify(
            company.canonical_name,
            chunks,
            quality_by_chunk,
        )
        facts, extraction_gaps, rejected_fact_chunks = await self.extractor.extract(
            company_id=company_id,
            domain_profile=domain_profile,
            selection=selection,
            chunks=chunks,
            sources=sources,
            versions=versions,
        )
        observations, interpreter_gaps = await self.interpreter.interpret(selection, facts)
        gaps = list(
            dict.fromkeys(
                classify_gaps
                + extraction_gaps
                + interpreter_gaps
                + (
                    [
                        f"当前 GENERAL chunks 共 {len(all_chunks)} 个；本次按上限处理最近 {len(chunks)} 个"
                    ]
                    if len(all_chunks) > len(chunks)
                    else []
                )
                + ([] if facts else ["当前 GENERAL 知识中没有可抽取的单来源科技事实"])
            )
        )
        warnings = (
            [f"忽略引用不存在输入集合的事实，chunk_id={item}" for item in rejected_fact_chunks]
            if rejected_fact_chunks
            else []
        )
        profile_id = _profile_id(company_id, all_chunks, self.processor_version, self.registry)
        profile = TechnologySemanticProfile(
            profile_id=profile_id,
            company_id=company_id,
            company_name=company.canonical_name,
            input_general_chunk_ids=[item.chunk_id for item in all_chunks],
            input_general_chunk_count=len(all_chunks),
            processed_general_chunk_count=len(chunks),
            domain_profile=domain_profile,
            template_selection=selection,
            technology_facts=facts,
            milestone_observations=observations,
            information_gaps=gaps,
            processor_version=self.processor_version,
            domain_registry_version=self.registry.domains.registry_version,
            template_registry_version=self.registry.templates.registry_version,
            standard_registry_version=self.registry.standards.registry_version,
            created_at=datetime.now(timezone.utc),
            processing_warnings=warnings,
        )

        await self._write_derived_chunks(profile, chunks, sources, versions)
        self.repository.save_technology_semantic_profile(profile)
        return profile

    async def _write_derived_chunks(
        self,
        profile: TechnologySemanticProfile,
        raw_chunks: list[KnowledgeChunk],
        sources: dict[str, Source],
        versions: dict[str, SourceVersion],
    ) -> None:
        raw_by_id = {chunk.chunk_id: chunk for chunk in raw_chunks}
        chunks_by_version: dict[str, list[KnowledgeChunk]] = {}
        lifecycle_fact_types = {
            fact_type.casefold()
            for template_id in profile.template_selection.selected_template_ids
            for milestone in self.registry.template_by_id[template_id].milestones
            for fact_type in milestone.fact_type_hints
        }
        for fact in profile.technology_facts:
            raw = raw_by_id[fact.source_chunk_id]
            source_version_id = raw.source_version_id
            if source_version_id is None:
                raise RuntimeError("Derived facts require a source version")
            layer = (
                KnowledgeLayer.LIFECYCLE
                if fact.fact_type.casefold() in lifecycle_fact_types
                else KnowledgeLayer.ENTERPRISE
            )
            text = (
                "派生原子科技事实（由模型从下列原始引用抽取，需回看引用核验）："
                f"{fact.subject}；{fact.predicate}；{fact.object_value}"
            )
            locator = fact.citation.locator
            derived = KnowledgeChunk(
                chunk_id=knowledge_chunk_id_for(source_version_id, locator, text),
                text=text,
                source_id=raw.source_id,
                source_version_id=source_version_id,
                citation=fact.citation,
                company_id=profile.company_id,
                knowledge_layer=layer,
                domain_tags=fact.domain_tags,
                template_tags=fact.template_tags,
                event_type=fact.fact_type,
                event_time=fact.event_time,
                entities=[fact.subject],
                metadata={
                    "semantic_kind": "technology_fact",
                    "derived_from_chunk_id": raw.chunk_id,
                    "fact_id": fact.fact_id,
                    "processor_version": profile.processor_version,
                    "source_quality": fact.source_quality.category,
                    "fact_type": fact.fact_type,
                    "derived_fact_payload": fact.model_dump(mode="json"),
                },
            )
            chunks_by_version.setdefault(source_version_id, []).append(derived)

        for version_id, derived_chunks in chunks_by_version.items():
            first = derived_chunks[0]
            source = sources[first.source_id]
            version = versions[version_id]
            company = self.repository.get_company(profile.company_id)
            await self.knowledge_base.upsert_source_version(
                source,
                version,
                derived_chunks,
                company=company,
            )


def _profile_id(
    company_id: str,
    chunks: list[KnowledgeChunk],
    processor_version: str,
    registry: KnowledgeSemanticRegistry,
) -> str:
    fingerprint = [
        company_id,
        sorted((item.chunk_id, item.source_version_id) for item in chunks),
        processor_version,
        registry.domains.registry_version,
        registry.templates.registry_version,
        registry.standards.registry_version,
    ]
    digest = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"tsp_{digest}"
