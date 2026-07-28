"""Embeddings client behind a Protocol.

Voyage rather than Anthropic: the Claude API has no embeddings endpoint, and
keeping the cheap pre-filter on a separate provider means it does not compete
with the tailoring budget.
"""

import hashlib
import math
from typing import Protocol

import httpx
from jobpilot_shared.settings import get_settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]: ...


class VoyageEmbeddingClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.voyage_api_key
        if not self._api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY is not set. Set it in .env, or run with "
                "JOBPILOT_FIXTURE_MODE=1 to use deterministic local embeddings."
            )
        self._model = model or settings.embedding_model

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": texts, "model": self._model, "input_type": input_type},
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


class FakeEmbeddingClient:
    """Deterministic hash-based embeddings.

    Not semantically meaningful, but stable and similarity-ordered enough that the
    pre-filter's plumbing can be exercised end-to-end without a network call:
    identical text always yields an identical vector.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or get_settings().embedding_dimensions

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [
            (digest[i % len(digest)] ^ (i * 31 % 251)) / 255.0 - 0.5 for i in range(self.dimensions)
        ]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


class NvidiaEmbeddingClient:
    """NVIDIA NIM embeddings, on the same key as the chat models.

    `nv-embedqa-e5-v5` returns 1024 dimensions — the same width as voyage-3, so
    switching providers needs no migration and no reindex. It also takes
    `input_type` as passage/query rather than document/query, which is the only
    wire difference worth knowing about.
    """

    _INPUT_TYPES = {"document": "passage", "query": "query"}

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from openai import OpenAI

        settings = get_settings()
        key = api_key if api_key is not None else settings.nvidia_api_key
        if not key:
            raise RuntimeError(
                "NVIDIA_API_KEY is not set. Put it in .env, or run with "
                "JOBPILOT_FIXTURE_MODE=1 for deterministic local embeddings."
            )
        self._model = model or settings.embedding_model
        self._client = OpenAI(
            api_key=key, base_url=settings.nvidia_base_url, timeout=60.0, max_retries=2
        )

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
            extra_body={"input_type": self._INPUT_TYPES.get(input_type, "passage")},
        )
        return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def get_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    if settings.fixture_mode:
        return FakeEmbeddingClient()
    if settings.embedding_provider == "voyage":
        return VoyageEmbeddingClient()
    return NvidiaEmbeddingClient()
