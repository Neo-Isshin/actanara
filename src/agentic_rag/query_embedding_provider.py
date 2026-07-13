"""Query embedding provider boundary for the RAG serving path.

The search server should depend on "turn text into vectors", not on a concrete
model object. Keeping this boundary small preserves existing retrieval behavior
while making the later server/process split explicit.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any


ModelFactory = Callable[[str], Any]


class LocalQueryEmbeddingProvider:
    """Lazy local SentenceTransformer-backed embedding provider."""

    def __init__(self, model_name: str, *, device: str | None = None, model_factory: ModelFactory | None = None):
        self.model_name = model_name
        self.device = device
        self._model_factory = model_factory or _sentence_transformer_factory
        self._model: Any | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> "LocalQueryEmbeddingProvider":
        if self._model is None:
            model = self._model_factory(self.model_name)
            if self.device and hasattr(model, "to"):
                model.to(self.device)
            self._model = model
        return self

    def encode(
        self,
        texts: Sequence[str],
        *,
        show_progress_bar: bool = False,
        timeout_seconds: float | None = None,
    ) -> list[list[float]]:
        self.load()
        embeddings = self._model.encode(list(texts), show_progress_bar=show_progress_bar)
        return _vectors_to_list(embeddings)

    def encode_query(self, query: str, *, timeout_seconds: float | None = None) -> list[float]:
        vectors = self.encode([query], show_progress_bar=False, timeout_seconds=timeout_seconds)
        return vectors[0] if vectors else []


def create_local_query_embedding_provider(model_name: str, *, device: str | None = None) -> LocalQueryEmbeddingProvider:
    return LocalQueryEmbeddingProvider(model_name, device=device)


class CloudQueryEmbeddingProvider:
    """Cloud embedding provider with no local model fallback."""

    def __init__(self, model_name: str, *, endpoint: str, api_key: str, timeout_seconds: float = 60):
        self.model_name = model_name
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = max(float(timeout_seconds), 0.1)

    @property
    def ready(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def load(self) -> "CloudQueryEmbeddingProvider":
        if not self.endpoint:
            raise RuntimeError("Cloud embedding endpoint is not configured")
        if not self.api_key:
            raise RuntimeError("Cloud embedding API key is not configured")
        return self

    def encode(
        self,
        texts: Sequence[str],
        *,
        show_progress_bar: bool = False,
        timeout_seconds: float | None = None,
    ) -> list[list[float]]:
        self.load()
        payload = json.dumps(
            {
                "model": self.model_name,
                "texts": [text if str(text).strip() else "empty" for text in texts],
                "type": "db",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        request_timeout = (
            min(self.timeout_seconds, max(float(timeout_seconds), 0.1))
            if timeout_seconds
            else self.timeout_seconds
        )
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        vectors = result.get("vectors") if isinstance(result, dict) else None
        return _vectors_to_list(vectors)

    def encode_query(self, query: str, *, timeout_seconds: float | None = None) -> list[float]:
        vectors = self.encode([query], show_progress_bar=False, timeout_seconds=timeout_seconds)
        return vectors[0] if vectors else []


def create_query_embedding_provider_from_config(config: Any, *, device: str | None = None) -> Any:
    if getattr(config, "PRODUCTION_MODE", "local") == "cloud":
        return CloudQueryEmbeddingProvider(
            getattr(config, "CLOUD_MODEL", getattr(config, "MODEL_NAME", "")),
            endpoint=getattr(config, "CLOUD_URL", ""),
            api_key=getattr(config, "CLOUD_API_KEY", ""),
            timeout_seconds=getattr(config, "SEARCH_LATENCY_BUDGET_SECONDS", 60),
        )
    return create_local_query_embedding_provider(getattr(config, "MODEL_NAME"), device=device)


def _sentence_transformer_factory(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def _vectors_to_list(value: Any) -> list[list[float]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        return []
    if not value:
        return []
    if isinstance(value[0], (int, float)):
        return [[float(item) for item in value]]
    vectors: list[list[float]] = []
    for vector in value:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if isinstance(vector, list):
            vectors.append([float(item) for item in vector])
    return vectors
