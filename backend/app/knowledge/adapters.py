"""Compatibility mapping from the current PDF pipeline to V2 contracts."""

from app.knowledge.contracts import (
    Citation,
    CitationLocator,
    KnowledgeChunk,
    KnowledgeLayer,
    Source,
    SourceType,
)
from app.knowledge.identity import (
    citation_id_for,
    knowledge_chunk_id_for,
    source_id_for,
    source_version_id_for,
)
from app.rag.document_parser import DocumentChunk, ParsedDocument


def parsed_document_to_source(document: ParsedDocument) -> Source:
    """Adapt a parsed PDF without carrying its machine-local filesystem path."""

    legacy_source = document.source
    content_sha256 = document.metadata.get("source_sha256")
    source_id = source_id_for(
        SourceType.UPLOADED_DOCUMENT,
        content_sha256=content_sha256,
    )
    metadata: dict[str, object] = {
        "document_id": legacy_source.document_id,
        "task_id": legacy_source.task_id,
        "file_name": legacy_source.file_name,
        "content_type": legacy_source.content_type,
    }
    for key in ("parser_version", "page_count"):
        if key in document.metadata:
            metadata[key] = document.metadata[key]

    return Source(
        source_id=source_id,
        source_type=SourceType.UPLOADED_DOCUMENT,
        title=document.document_name,
        content_sha256=content_sha256,
        metadata=metadata,
    )


def document_chunk_to_knowledge_chunk(
    chunk: DocumentChunk,
    source: Source,
    *,
    company_id: str | None = None,
    knowledge_layer: KnowledgeLayer = KnowledgeLayer.ENTERPRISE,
) -> KnowledgeChunk:
    """Map one legacy page chunk while retaining its exact text and location.

    The legacy ``chunk_id`` is preserved for compatibility even though its
    current generator includes ``task_id``. New shared-index identities should
    be assigned independently when the storage migration is designed.
    """

    if source.source_type != SourceType.UPLOADED_DOCUMENT:
        raise ValueError("Legacy PDF chunks require an uploaded_document source")
    source_document_id = source.metadata.get("document_id")
    if source_document_id is not None and source_document_id != chunk.document_id:
        raise ValueError("DocumentChunk document_id does not match its Source")

    content_sha256 = chunk.metadata.get("source_sha256") or source.content_sha256
    if source.content_sha256 and content_sha256 != source.content_sha256:
        raise ValueError("DocumentChunk content hash does not match its Source")
    if content_sha256 is None:
        raise ValueError("A content hash is required to create a persistent PDF chunk")
    source_version_id = source_version_id_for(source.source_id, content_sha256)
    locator = CitationLocator(
        page_number=chunk.page_number,
        locator_text=chunk.locator,
        url=source.canonical_url,
    )
    citation_id = citation_id_for(source_version_id, locator, chunk.text)
    citation = Citation(
        citation_id=citation_id,
        source_id=source.source_id,
        source_version_id=source_version_id,
        source_url=source.canonical_url,
        source_title=source.title,
        excerpt=chunk.text,
        locator=locator,
    )

    metadata: dict[str, object] = {
        "task_id": chunk.task_id,
        "document_id": chunk.document_id,
        "document_name": chunk.document_name,
        "legacy_chunk_id": chunk.chunk_id,
    }
    source_sha256 = content_sha256
    if source_sha256 is not None:
        metadata["source_sha256"] = source_sha256
    if "parser_version" in chunk.metadata:
        metadata["parser_version"] = chunk.metadata["parser_version"]

    return KnowledgeChunk(
        chunk_id=knowledge_chunk_id_for(source_version_id, locator, chunk.text),
        text=chunk.text,
        source_id=source.source_id,
        source_version_id=source_version_id,
        citation=citation,
        company_id=company_id,
        knowledge_layer=knowledge_layer,
        metadata=metadata,
    )
