from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from google.genai import types

from app import embeddings
from app.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EMBEDDING_REASON,
    EmbeddingError,
    format_embedding_for_database,
    generate_embedding,
)


def _vector(seed: float = 0.01, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    return [round(seed * (index + 1), 6) for index in range(dimensions)]


def _response(*vectors: list[float]) -> types.EmbedContentResponse:
    return types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=vector) for vector in vectors]
    )


def _raw_response(*vectors: list[Any]) -> types.EmbedContentResponse:
    """Build a response whose values skip SDK validation to mimic a malformed payload."""
    return types.EmbedContentResponse.model_construct(
        embeddings=[types.ContentEmbedding.model_construct(values=vector) for vector in vectors]
    )


class FakeEmbeddingModels:
    def __init__(self, results: list[Any]):
        self.results = results
        self.requests: list[dict[str, Any]] = []

    def embed_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        result = self.results[len(self.requests) - 1]
        if isinstance(result, Exception):
            raise result
        return result


class FakeGeminiClient:
    def __init__(self, *results: Any):
        self.models = FakeEmbeddingModels(list(results))


@pytest.fixture
def gemini(monkeypatch):
    """Install a fake Gemini client so no test ever reaches the real API."""

    def _install(*results: Any) -> FakeGeminiClient:
        client = FakeGeminiClient(*results)
        monkeypatch.setattr(embeddings, "get_gemini_client", lambda: client)
        return client

    return _install


def test_generate_embedding_returns_the_gemini_vector_for_one_chunk(gemini):
    client = gemini(_response(_vector(0.5)))

    vector = generate_embedding("Chunk about Nova")

    assert EMBEDDING_MODEL == "gemini-embedding-2"
    assert EMBEDDING_DIMENSIONS == 768
    assert len(vector) == EMBEDDING_DIMENSIONS
    assert all(isinstance(value, float) for value in vector)
    assert client.models.requests[0]["model"] == EMBEDDING_MODEL
    assert client.models.requests[0]["contents"] == "Chunk about Nova"
    assert client.models.requests[0]["config"].output_dimensionality == EMBEDDING_DIMENSIONS


def test_generate_embedding_rejects_an_unexpected_dimension(gemini):
    gemini(_response(_vector(1.0, dimensions=1536)))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON
    assert "1536" not in exc_info.value.reason


def test_generate_embedding_rejects_aggregated_embeddings(gemini):
    gemini(_response(_vector(), _vector(0.5)))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON


def test_generate_embedding_rejects_a_missing_embedding(gemini):
    gemini(_response())

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON


def test_generate_embedding_rejects_non_numeric_values(gemini):
    values = _vector()
    values[5] = "not-a-number"
    gemini(_raw_response(values))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON


def test_generate_embedding_wraps_provider_failures_in_a_safe_reason(gemini):
    gemini(RuntimeError("429 rate limit for https://internal.example/v1beta?key=secret-key"))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    reason = exc_info.value.reason
    assert reason == EMBEDDING_REASON
    for leaked in ("secret-key", "https://", "429", "RuntimeError"):
        assert leaked not in reason


def test_generate_embedding_rejects_empty_chunk_text_without_calling_gemini(gemini):
    client = gemini(_response(_vector()))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("   \n\t ")

    assert exc_info.value.reason == EMBEDDING_REASON
    assert client.models.requests == []


def test_generate_embedding_rejects_non_finite_values(gemini):
    values = _vector()
    values[0] = float("inf")
    gemini(_response(values))

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON


def test_generate_embedding_requires_a_configured_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    embeddings.get_gemini_client.cache_clear()

    with pytest.raises(EmbeddingError) as exc_info:
        generate_embedding("Chunk")

    assert exc_info.value.reason == EMBEDDING_REASON
    assert "GEMINI_API_KEY" not in exc_info.value.reason


def test_generate_embedding_sends_arabic_chunk_text_unchanged(gemini):
    client = gemini(_response(_vector(0.25)))

    vector = generate_embedding("محتوى عربي لاختبار التضمين")

    assert client.models.requests[0]["contents"] == "محتوى عربي لاختبار التضمين"
    assert len(vector) == EMBEDDING_DIMENSIONS


def test_format_embedding_for_database_produces_a_pgvector_literal():
    assert format_embedding_for_database([0.5, -1.0, 0.0]) == "[0.5,-1.0,0.0]"

    literal = format_embedding_for_database(_vector())
    assert literal.startswith("[") and literal.endswith("]")
    assert " " not in literal
    assert [float(part) for part in literal.strip("[]").split(",")] == _vector()


def test_embeddings_module_reads_the_key_from_the_backend_environment_only():
    source = (Path(__file__).parents[1] / "app/embeddings.py").read_text(encoding="utf-8")

    assert 'os.getenv("GEMINI_API_KEY")' in source
    assert "NEXT_PUBLIC_GEMINI_API_KEY" not in source
    assert "api_key=api_key" in source


def test_backend_environment_template_documents_the_key_without_a_value():
    template = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")

    assert "GEMINI_API_KEY=" in template
    assert re.search(r"GEMINI_API_KEY=\S+", template) is None


def test_frontend_never_receives_the_gemini_key():
    frontend = Path(__file__).parents[2] / "frontend"

    assert "GEMINI" not in (frontend / ".env.example").read_text(encoding="utf-8")

    for directory in ("app", "components", "lib"):
        for path in (frontend / directory).rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx"}:
                assert "GEMINI" not in path.read_text(encoding="utf-8")


def test_docker_compose_passes_the_gemini_key_to_the_backend_container_only():
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")

    frontend_section, backend_section = compose.split("  backend:", 1)
    assert "GEMINI" not in frontend_section
    assert "GEMINI_API_KEY: ${GEMINI_API_KEY}" in backend_section
