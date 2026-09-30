"""End-to-end raw GENERAL → adaptive technology semantics → derived knowledge."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from app.llm import StructuredJSONModel
from app.knowledge.contracts import (
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceVersion,
)
from app.knowledge.identity import derived_fact_chunk_id_for, ensure_unique_chunk_ids
from app.knowledge.semantic.classifier import TechnologyDomainClassifier
from app.knowledge.semantic.contracts import TechnologySemanticProfile
from app.knowledge.semantic.evidence_selector import (
    select_semantic_evidence,
    select_template_fact_evidence,
)
from app.knowledge.semantic.extractor import TechnologyFactExtractor, source_quality
from app.knowledge.semantic.interpreter import TechnologyInterpreter
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.shared_knowledge_base import SharedKnowledgeBase

PROCESSOR_VERSION = "technology-semantic.v8"


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
        self.last_execution_trace: dict[str, object] = {}

    async def process_company(self, company_id: str) -> TechnologySemanticProfile:
        company = self.repository.get_company(company_id)
        if company is None:
            raise ValueError(f"Unknown company_id: {company_id}")
        all_chunks = self.repository.list_current_chunks(company_id, KnowledgeLayer.GENERAL.value)
        self.last_execution_trace = {
            "semantic_stage": "evidence_selection",
            "semantic_substage": "evidence_selection",
            "available_chunk_count": len(all_chunks),
            "technology_fact_type_registry_version": self.registry.fact_types.registry_version,
        }
        sources: dict[str, Source] = {}
        versions: dict[str, SourceVersion] = {}
        quality_objects = {}
        for chunk in all_chunks:
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
            quality_objects[chunk.chunk_id] = source_quality(source, chunk, version)

        classifier_evidence = select_semantic_evidence(all_chunks, quality_objects)
        classifier_chunks = classifier_evidence.original_chunks
        classifier_model_chunks = classifier_evidence.model_chunks
        classifier_quality_by_chunk = classifier_evidence.quality_by_chunk
        classifier_selection_report = classifier_evidence.report
        self.last_execution_trace = {
            "semantic_stage": "classifier",
            "semantic_substage": "domain_template_classifier",
            "technology_fact_type_registry_version": self.registry.fact_types.registry_version,
            **classifier_selection_report.model_dump(mode="json"),
            "classifier_evidence_selection": classifier_selection_report.model_dump(mode="json"),
            "classifier_available_chunk_count": classifier_selection_report.available_chunk_count,
            "classifier_selected_chunk_count": classifier_selection_report.selected_chunk_count,
            "classifier_selected_source_count": classifier_selection_report.selected_source_count,
            "classifier_selected_char_count": classifier_selection_report.selected_char_count,
            "classifier_quality_distribution": classifier_selection_report.quality_distribution,
        }

        domain_profile, selection, classify_gaps = await self.classifier.classify(
            company.canonical_name,
            classifier_model_chunks,
            classifier_quality_by_chunk,
        )
        classifier_report = self.classifier.last_report
        self.last_execution_trace.update(
            classifier_status=domain_profile.status,
            classifier_input_evidence_count=classifier_report.input_evidence_count,
            classifier_invalid_evidence_refs=classifier_report.invalid_domain_evidence_refs + classifier_report.invalid_template_evidence_refs,
            classifier_invalid_domain_evidence_reference_count=classifier_report.invalid_domain_evidence_reference_count,
            classifier_invalid_template_evidence_reference_count=classifier_report.invalid_template_evidence_reference_count,
            classifier_dropped_templates=classifier_report.dropped_template_count,
            classifier_downgraded=classifier_report.downgraded_domain_classification,
            classifier_report=classifier_report.model_dump(mode="json"),
            primary_domains=domain_profile.primary_domains,
            secondary_domains=domain_profile.secondary_domains,
            selected_template_ids=selection.selected_template_ids,
        )
        fact_evidence = select_template_fact_evidence(
            all_chunks,
            quality_objects,
            self.registry,
            selection.selected_template_ids,
            classifier_chunks=classifier_chunks,
        )
        fact_chunks = fact_evidence.original_chunks
        fact_model_chunks = fact_evidence.model_chunks
        fact_selection_report = fact_evidence.report
        self.last_execution_trace.update(
            semantic_stage="fact_extractor",
            semantic_substage="template_fact_evidence_selection",
            fact_evidence_selection=fact_selection_report.model_dump(mode="json"),
            fact_available_chunk_count=fact_selection_report.available_chunk_count,
            fact_positive_match_candidate_count=fact_selection_report.positive_match_candidate_count,
            fact_positive_match_selected_count=fact_selection_report.positive_match_selected_count,
            fact_selected_chunk_count=fact_selection_report.selected_chunk_count,
            fact_selected_source_count=fact_selection_report.selected_source_count,
            fact_selected_char_count=fact_selection_report.selected_char_count,
            fact_quality_distribution=fact_selection_report.quality_distribution,
            fact_fallback_fill_count=fact_selection_report.fallback_fill_count,
            fact_selected_template_ids=fact_selection_report.selected_template_ids,
            fact_template_match_score_distribution=fact_selection_report.positive_match_score_distribution,
            fact_processed_chunk_count=len(fact_chunks),
        )
        try:
            self.last_execution_trace["semantic_substage"] = "technology_fact_extractor"
            facts, extraction_gaps, rejected_fact_chunks, rejected_fact_tags, extraction_report = await self.extractor.extract(
                company_id=company_id,
                domain_profile=domain_profile,
                selection=selection,
                chunks=fact_model_chunks,
                sources=sources,
                versions=versions,
            )
        except Exception:
            self.last_execution_trace.update(self.extractor.last_execution_trace)
            raise
        self.last_execution_trace.update(self.extractor.last_execution_trace)
        self.last_execution_trace.update(
            technology_fact_count=len(facts),
            fact_type_distribution=dict(Counter(fact.fact_type for fact in facts)),
            source_quality_distribution=dict(Counter(fact.source_quality.category for fact in facts)),
        )
        self.last_execution_trace.update(
            semantic_stage="interpreter",
            semantic_substage="technology_interpreter",
        )
        observations, interpreter_gaps = await self.interpreter.interpret(selection, facts)
        interpreter_report = self.interpreter.last_report
        self.last_execution_trace.update(
            semantic_stage="persistence",
            semantic_substage="derived_chunk_persistence",
            interpreter_status="completed",
            **{
                f"interpreter_{key}": value
                for key, value in interpreter_report.model_dump(mode="json").items()
            },
            persistence_phase="profile_assembly",
            milestone_observation_count=len(observations),
            milestone_status_distribution=dict(
                Counter(item.status for item in observations)
            ),
            blocked_inference_count=sum(
                len(item.blocked_inferences) for item in observations
            ),
        )
        gaps = list(
            dict.fromkeys(
                classify_gaps
                + extraction_gaps
                + interpreter_gaps
                + (
                    [
                        "当前 GENERAL chunks 共 {available} 个；semantic selector 在来源多样性与字符预算约束下选择 {selected} 个，覆盖 {sources} 个来源、{chars} 个字符。".format(
                            available=len(all_chunks),
                            selected=classifier_selection_report.selected_chunk_count,
                            sources=classifier_selection_report.selected_source_count,
                            chars=classifier_selection_report.selected_char_count,
                        )
                    ]
                    if len(all_chunks) > len(classifier_chunks) or classifier_selection_report.truncated_chunk_count
                    else []
                )
                + (
                    [
                        "科技事实抽取使用模板感知证据选择：模板词命中 {positive} 个候选，选择 {selected} 个 chunk，回退补充 {fallback} 个。".format(
                            positive=fact_selection_report.positive_match_candidate_count,
                            selected=fact_selection_report.selected_chunk_count,
                            fallback=fact_selection_report.fallback_fill_count,
                        )
                    ]
                    if len(all_chunks) > len(fact_chunks) or fact_selection_report.fallback_fill_count
                    else []
                )
                + ([] if facts else ["当前 GENERAL 知识中没有可抽取的单来源科技事实"])
            )
        )
        warnings = list(classifier_report.warnings)
        warnings.extend(f"忽略引用不存在当前 batch 输入集合的事实，chunk_id={item}" for item in rejected_fact_chunks)
        if rejected_fact_chunks:
            warnings.append(f"invalid_batch_chunk_reference:{len(rejected_fact_chunks)}")
        warnings.extend(f"忽略未注册标签的事实，chunk_id={item}" for item in rejected_fact_tags)
        if extraction_report.rejected_fact_type_count:
            warnings.append(
                f"technology_fact_type_rejected:{extraction_report.rejected_fact_type_count}"
            )
        if extraction_report.failed_batch_count:
            warnings.append(
                f"fact_extraction_batch_failed:{extraction_report.failed_batch_count}/{extraction_report.batch_count}"
            )
        profile_id = _profile_id(company_id, all_chunks, self.processor_version, self.registry)
        profile = TechnologySemanticProfile(
            profile_id=profile_id,
            company_id=company_id,
            company_name=company.canonical_name,
            input_general_chunk_ids=[item.chunk_id for item in all_chunks],
            input_general_chunk_count=len(all_chunks),
            processed_general_chunk_count=len({item.chunk_id for item in classifier_chunks + fact_chunks}),
            classifier_processed_chunk_count=len(classifier_chunks),
            fact_processed_chunk_count=len(fact_chunks),
            semantic_evidence_selection=classifier_selection_report,
            classifier_evidence_selection=classifier_selection_report,
            fact_evidence_selection=fact_selection_report,
            classifier_report=classifier_report,
            fact_extraction_report=extraction_report,
            domain_profile=domain_profile,
            template_selection=selection,
            technology_facts=facts,
            interpreter_report=interpreter_report,
            milestone_observations=observations,
            information_gaps=gaps,
            processor_version=self.processor_version,
            domain_registry_version=self.registry.domains.registry_version,
            template_registry_version=self.registry.templates.registry_version,
            standard_registry_version=self.registry.standards.registry_version,
            technology_fact_type_registry_version=self.registry.fact_types.registry_version,
            created_at=datetime.now(timezone.utc),
            processing_warnings=warnings,
        )

        raw_by_id = {chunk.chunk_id: chunk for chunk in fact_chunks}
        lifecycle_fact_types = self._lifecycle_fact_types(profile)
        derived_ids = []
        lifecycle_count = 0
        for fact in facts:
            raw = raw_by_id[fact.source_chunk_id]
            if raw.source_version_id is None:
                raise RuntimeError("Derived facts require a source version")
            derived_ids.append(
                derived_fact_chunk_id_for(raw.source_version_id, fact.fact_id)
            )
            if fact.fact_type.casefold() in lifecycle_fact_types:
                lifecycle_count += 1
        self.last_execution_trace.update(
            derived_fact_chunk_count=len(facts),
            derived_lifecycle_chunk_count=lifecycle_count,
            derived_enterprise_chunk_count=len(facts) - lifecycle_count,
            derived_unique_chunk_id_count=len(set(derived_ids)),
            persistence_phase="derived_chunk_persistence",
        )
        ensure_unique_chunk_ids(derived_ids)
        await self._write_derived_chunks(profile, fact_chunks, sources, versions)
        self.last_execution_trace.update(
            semantic_stage="persistence",
            semantic_substage="semantic_profile_persistence",
            persistence_phase="semantic_profile_persistence",
        )
        self.repository.save_technology_semantic_profile(profile)
        self.last_execution_trace.update(
            semantic_stage="complete",
            semantic_substage="complete",
            persistence_phase="complete",
        )
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
        lifecycle_fact_types = self._lifecycle_fact_types(profile)
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
                chunk_id=derived_fact_chunk_id_for(source_version_id, fact.fact_id),
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

        for derived_chunks in chunks_by_version.values():
            ensure_unique_chunk_ids(chunk.chunk_id for chunk in derived_chunks)

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

    def _lifecycle_fact_types(self, profile: TechnologySemanticProfile) -> set[str]:
        return {
            fact_type.casefold()
            for template_id in profile.template_selection.selected_template_ids
            for milestone in self.registry.template_by_id[template_id].milestones
            for fact_type in milestone.fact_type_hints
        }


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
        registry.fact_types.registry_version,
    ]
    digest = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"tsp_{digest}"
