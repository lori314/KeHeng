"""Local v0.2 RAG building blocks."""

from app.rag.document_parser import DocumentParser, PdfDocumentParser
from app.rag.embedding import EmbeddingProvider, LocalHashingEmbeddingProvider
from app.rag.knowledge_base import ChromaKnowledgeBase, KnowledgeBase
from app.rag.retrieval import KnowledgeBaseRetriever, Retriever

__all__ = [
    "DocumentParser",
    "PdfDocumentParser",
    "EmbeddingProvider",
    "LocalHashingEmbeddingProvider",
    "Retriever",
    "KnowledgeBaseRetriever",
    "KnowledgeBase",
    "ChromaKnowledgeBase",
]
