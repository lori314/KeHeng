"""Provider-neutral and local deterministic embedding implementations."""

from abc import ABC, abstractmethod

from pydantic import BaseModel
from sklearn.feature_extraction.text import HashingVectorizer


class EmbeddingRequest(BaseModel):
    texts: list[str]
    model_version: str = "local-char-ngram-v1"


class EmbeddingBatch(BaseModel):
    vectors: list[list[float]]
    dimensions: int
    model_version: str


class EmbeddingProvider(ABC):
    """Provider-neutral vectorization boundary."""

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        raise NotImplementedError


class LocalHashingEmbeddingProvider(EmbeddingProvider):
    """Lightweight, offline char n-gram embeddings suitable for Chinese text.

    This is a deterministic retrieval baseline, not a semantic foundation model.
    It requires no model download and can later be replaced behind the same API.
    """

    model_version = "local-char-ngram-v1"

    def __init__(self, dimensions: int = 1024) -> None:
        if dimensions < 128:
            raise ValueError("dimensions must be at least 128")
        self.dimensions = dimensions
        self._vectorizer = HashingVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            n_features=dimensions,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not request.texts:
            return EmbeddingBatch(
                vectors=[],
                dimensions=self.dimensions,
                model_version=self.model_version,
            )
        matrix = self._vectorizer.transform(request.texts)
        vectors = matrix.astype("float32").toarray().tolist()
        return EmbeddingBatch(
            vectors=vectors,
            dimensions=self.dimensions,
            model_version=self.model_version,
        )
