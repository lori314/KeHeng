"""PDF loading and page-aware text splitting."""

from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path

import pymupdf
from pydantic import BaseModel, Field


class DocumentSource(BaseModel):
    task_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    local_path: str = Field(min_length=1)


class ParsedPage(BaseModel):
    page_number: int
    text: str


class ParsedDocument(BaseModel):
    source: DocumentSource
    document_name: str
    pages: list[ParsedPage]
    metadata: dict[str, str] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    task_id: str
    document_id: str
    document_name: str
    page_number: int | None
    chunk_id: str
    text: str
    locator: str
    metadata: dict[str, str] = Field(default_factory=dict)


class DocumentParser(ABC):
    """Replaceable parser that must preserve source location metadata."""

    @abstractmethod
    async def load(self, source: DocumentSource) -> ParsedDocument:
        raise NotImplementedError

    @abstractmethod
    async def split(self, document: ParsedDocument) -> list[DocumentChunk]:
        raise NotImplementedError


class PdfDocumentParser(DocumentParser):
    """Extract text from text-based PDFs and preserve page provenance."""

    parser_version = "pymupdf-v1"

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 100) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size must be at least 100 characters")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def load(self, source: DocumentSource) -> ParsedDocument:
        path = Path(source.local_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"PDF not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF input is supported in v0.2: {path.name}")

        file_hash = sha256(path.read_bytes()).hexdigest()
        pages: list[ParsedPage] = []
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError("Password-protected PDFs are not supported in v0.2")
            for index, page in enumerate(pdf, start=1):
                text = self._normalize_text(page.get_text("text", sort=True))
                if text:
                    pages.append(ParsedPage(page_number=index, text=text))
            page_count = pdf.page_count

        if not pages:
            raise ValueError(
                "No extractable text found. Scanned PDFs require a future OCR pipeline."
            )

        return ParsedDocument(
            source=source,
            document_name=source.file_name or path.name,
            pages=pages,
            metadata={
                "source_sha256": file_hash,
                "page_count": str(page_count),
                "parser_version": self.parser_version,
            },
        )

    async def split(self, document: ParsedDocument) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        source_hash = document.metadata.get("source_sha256", "unknown")

        for page in document.pages:
            for chunk_index, text in enumerate(self._split_page(page.text), start=1):
                digest_input = (
                    f"{document.source.task_id}|{document.source.document_id}|"
                    f"{source_hash}|{page.page_number}|{chunk_index}|{text}"
                )
                chunk_id = sha256(digest_input.encode("utf-8")).hexdigest()[:24]
                chunks.append(
                    DocumentChunk(
                        task_id=document.source.task_id,
                        document_id=document.source.document_id,
                        document_name=document.document_name,
                        page_number=page.page_number,
                        chunk_id=chunk_id,
                        text=text,
                        locator=f"第 {page.page_number} 页 / 片段 {chunk_index}",
                        metadata={
                            "source_sha256": source_hash,
                            "parser_version": self.parser_version,
                        },
                    )
                )

        return chunks

    def _split_page(self, text: str) -> list[str]:
        chunks: list[str] = []
        start = 0

        while start < len(text):
            target_end = min(start + self.chunk_size, len(text))
            end = target_end
            if target_end < len(text):
                search_start = start + self.chunk_size // 2
                boundaries = [
                    text.rfind(marker, search_start, target_end)
                    for marker in ("\n", "。", "；", "！", "？", ". ")
                ]
                best_boundary = max(boundaries)
                if best_boundary >= search_start:
                    end = best_boundary + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - self.chunk_overlap, start + 1)

        return chunks

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [" ".join(line.split()) for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()
