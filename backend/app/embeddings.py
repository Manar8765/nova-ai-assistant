"""Gemini Embedding 2 client used to turn document chunks into stored vectors."""

from __future__ import annotations

import logging
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_TIMEOUT_MS = 30_000

# Safe message stored in document.failure_reason. Provider and configuration details
# are only written to the backend logs.
EMBEDDING_REASON = (
    "We couldn't prepare this document's content for search due to a temporary system error. "
    "Please try again."
)


class EmbeddingError(RuntimeError):
    """Raised when a chunk embedding cannot be produced or validated."""

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@lru_cache
def get_gemini_client() -> genai.Client:
    """Build the server-only Gemini client. The API key is never exposed to the frontend."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY is not configured; document chunks cannot be embedded.")
        raise EmbeddingError(EMBEDDING_REASON)

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=EMBEDDING_TIMEOUT_MS))


def generate_embedding(text: str) -> list[float]:
    """Return the validated embedding of one chunk.

    Gemini Embedding 2 aggregates several inputs into a single vector, so every chunk is
    embedded with its own request and the response must hold exactly one vector with
    ``EMBEDDING_DIMENSIONS`` values.
    """
    if not text or not text.strip():
        logger.error("Refusing to embed an empty chunk.")
        raise EmbeddingError(EMBEDDING_REASON)

    try:
        response = get_gemini_client().models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
        )
    except EmbeddingError:
        raise
    except Exception as exc:
        # Rate limits, network errors and provider failures stay in the logs.
        logger.exception("Gemini embedding request failed.")
        raise EmbeddingError(EMBEDDING_REASON, retryable=True) from exc

    return _validated_vector(response)


def _validated_vector(response: Any) -> list[float]:
    embeddings = getattr(response, "embeddings", None)
    if not embeddings or len(embeddings) != 1:
        logger.error("Gemini returned %s embeddings for a single chunk.", len(embeddings or []))
        raise EmbeddingError(EMBEDDING_REASON)

    values = getattr(embeddings[0], "values", None)
    if values is None or len(values) != EMBEDDING_DIMENSIONS:
        logger.error("Gemini returned an embedding with %s values.", len(values or []))
        raise EmbeddingError(EMBEDDING_REASON)

    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        logger.error("Gemini returned an embedding with non-numeric values.")
        raise EmbeddingError(EMBEDDING_REASON) from exc

    if not all(math.isfinite(value) for value in vector):
        logger.error("Gemini returned an embedding with non-finite values.")
        raise EmbeddingError(EMBEDDING_REASON)

    return vector


def format_embedding_for_database(vector: Sequence[float]) -> str:
    """Render a vector as the pgvector literal persisted in document_chunk.embedding."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"
