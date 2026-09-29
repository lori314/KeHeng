"""Extract one-source atomic technology facts from raw GENERAL chunks."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from app.llm import StructuredJSONModel
from app.knowledge.contracts import KnowledgeChunk, Source, SourceVersion
from app.knowledge.semantic.contracts import (
    FactExtractionOutput,
    SourceQuality,
    TechnologyDomainProfile,
    TechnologyFact,
    TechnologyTemplateSelection,
)
from app.knowledge.semantic.registry import KnowledgeSemanticRegistry
from app.knowledge.semantic.structured_call import complete_contract


class TechnologyFactExtractor:
    def __init__(
        self,
        model: StructuredJSONModel,
        registry: KnowledgeSemanticRegistry,
        *,
        processor_version: str,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        self.processor_version = processor_version
        path = prompt_path or _prompt_path("technology_fact_extractor_prompt.md")
        self.prompt = Path(path).read_text(encoding="utf-8")

    async def extract(
        self,
        *,
        company_id: str,
        domain_profile: TechnologyDomainProfile,
        selection: TechnologyTemplateSelection,
        chunks: list[KnowledgeChunk],
        sources: dict[str, Source],
        versions: dict[str, SourceVersion],
    ) -> tuple[list[TechnologyFact], list[str], list[str]]:
        if not selection.selected_template_ids or not chunks:
            return [], [], []
        template_data = [
            self.registry.template_by_id[item].model_dump(mode="json")
            for item in selection.selected_template_ids
        ]
        contexts = []
        quality_by_id: dict[str, SourceQuality] = {}
        for chunk in chunks:
            source = sources[chunk.source_id]
            version = versions[chunk.source_version_id or ""]
            quality = source_quality(source, chunk, version)
            quality_by_id[chunk.chunk_id] = quality
            contexts.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text[:6000],
                    "source_quality": quality.model_dump(mode="json"),
                    "citation": chunk.citation.model_dump(mode="json"),
                }
            )
        output = await complete_contract(
            self.model,
            self.prompt,
            {
                "company_id": company_id,
                "domain_profile": domain_profile.model_dump(mode="json"),
                "selected_templates": template_data,
                "general_chunks": contexts,
            },
            FactExtractionOutput,
            stage="technology fact extractor",
        )
        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        valid_facts: list[TechnologyFact] = []
        rejected_evidence: list[str] = []
        rejected_tags: list[str] = []
        domain_ids = set(domain_profile.primary_domains + domain_profile.secondary_domains)
        selected_ids = set(selection.selected_template_ids)
        for draft in output.facts:
            chunk = chunks_by_id.get(draft.source_chunk_id)
            if chunk is None:
                rejected_evidence.append(draft.source_chunk_id)
                continue
            if not set(draft.domain_tags).issubset(domain_ids) or not set(draft.template_tags).issubset(selected_ids):
                rejected_tags.append(draft.source_chunk_id)
                continue
            fact_id = stable_fact_id(
                company_id,
                chunk.chunk_id,
                {
                    "subject": _normalized(draft.subject),
                    "predicate": _normalized(draft.predicate),
                    "object_value": _normalized(draft.object_value),
                    "fact_type": draft.fact_type.strip().casefold(),
                    "event_time": draft.event_time.isoformat() if draft.event_time else None,
                    "quantitative_value": draft.quantitative_value,
                    "quantitative_unit": draft.quantitative_unit,
                    "domain_tags": sorted(set(draft.domain_tags)),
                    "template_tags": sorted(set(draft.template_tags)),
                },
                self.processor_version,
                self.registry.templates.registry_version,
            )
            valid_facts.append(
                TechnologyFact(
                    fact_id=fact_id,
                    company_id=company_id,
                    subject=draft.subject.strip(),
                    predicate=draft.predicate.strip(),
                    object_value=draft.object_value.strip(),
                    fact_type=draft.fact_type.strip(),
                    event_time=draft.event_time,
                    quantitative_value=draft.quantitative_value,
                    quantitative_unit=draft.quantitative_unit,
                    source_chunk_id=chunk.chunk_id,
                    citation=chunk.citation,
                    domain_tags=draft.domain_tags,
                    template_tags=draft.template_tags,
                    source_quality=quality_by_id[chunk.chunk_id],
                    processor_version=self.processor_version,
                )
            )
        unique = {fact.fact_id: fact for fact in valid_facts}
        return list(unique.values()), output.information_gaps, rejected_evidence + rejected_tags


def source_quality(source: Source, chunk: KnowledgeChunk, version: SourceVersion) -> SourceQuality:
    scope = str(
        chunk.metadata.get("content_scope")
        or version.metadata.get("content_scope")
        or "full_content"
    )
    if scope == "search_snippet":
        category = "snippet_only"
        rationale = "仅搜索摘要，证据等级最低，不作强里程碑依据。"
    elif source.source_type.value in {"government", "regulatory", "registry", "standard"}:
        category = "authoritative_public_record"
        rationale = "根据来源类型确定为公开记录类来源；仍须核对其与事实的对应关系。"
    elif source.source_type.value == "company_official":
        category = "first_party"
        rationale = "来源类型标记为企业一方来源。"
    elif source.source_type.value in {"paper", "patent"}:
        category = "academic_or_patent"
        rationale = "来源类型标记为论文或专利。"
    elif source.source_type.value == "news":
        category = "third_party"
        rationale = "来源类型标记为新闻媒体。"
    elif source.source_type.value == "web":
        category = "weak_web"
        rationale = "通用网页来源尚无可验证的权威等级元数据。"
    else:
        category = "weak_web"
        rationale = "未分类来源按较弱网页证据处理。"
    return SourceQuality(
        category=category,
        source_type=source.source_type.value,
        content_scope=scope,
        rationale=rationale,
    )


def stable_fact_id(
    company_id: str,
    source_chunk_id: str,
    atom: dict,
    processor_version: str,
    template_registry_version: str,
) -> str:
    payload = json.dumps(
        [company_id, source_chunk_id, atom, processor_version, template_registry_version],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"tf_{digest}"


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "prompts" / name
