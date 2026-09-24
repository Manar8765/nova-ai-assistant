"""Company-scoped vector retrieval of document chunks for grounded RAG answers."""

from __future__ import annotations

import logging
import math
from typing import Any
from uuid import UUID

from supabase import Client

logger = logging.getLogger(__name__)

# MVP retrieval settings. The threshold is a starting value to be tuned during Phase 11
# validation, so it is kept in a single place instead of being spread across calls.
RAG_TOP_K = 5
RAG_SIMILARITY_THRESHOLD = 0.5

RETRIEVAL_REASON = (
    "We couldn't search the company knowledge base due to a temporary system error. "
    "Please try again."
)
REQUIRED_RESULT_FIELDS = ("document_id", "chunk_id", "filename", "chunk_index", "content", "similarity")


class RetrievalError(RuntimeError):
    """Raised when company knowledge chunks cannot be retrieved."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def retrieve_chunks(client: Client, company_id: str, query_embedding: str) -> list[dict[str, Any]]:
    """Return the company's most similar READY chunks for a query embedding.

    Tenant isolation happens inside the SQL function: the service-role client bypasses row
    level security, so ``company_id`` must be enforced by the retrieval query itself.
    """
    try:
        response = client.rpc(
            "match_document_chunks",
            {
                "p_company_id": company_id,
                "p_query_embedding": query_embedding,
                "p_match_count": RAG_TOP_K,
                "p_similarity_threshold": RAG_SIMILARITY_THRESHOLD,
            },
        ).execute()
    except Exception as exc:
        logger.exception("Knowledge base retrieval failed.")
        raise RetrievalError(RETRIEVAL_REASON) from exc

    rows = response.data or []
    if not isinstance(rows, list):
        logger.error("Knowledge base retrieval returned a non-list result.")
        raise RetrievalError(RETRIEVAL_REASON)
    valid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(field not in row for field in REQUIRED_RESULT_FIELDS):
            logger.error("Knowledge base retrieval returned malformed row at index=%s.", index)
            raise RetrievalError(RETRIEVAL_REASON)
        try:
            UUID(str(row["document_id"]))
            UUID(str(row["chunk_id"]))
            if not isinstance(row["filename"], str) or not isinstance(row["content"], str):
                raise ValueError
            row["similarity"] = float(row["similarity"])
            row["chunk_index"] = int(row["chunk_index"])
            if not math.isfinite(row["similarity"]) or row["chunk_index"] < 0:
                raise ValueError
        except (TypeError, ValueError):
            logger.error("Knowledge base retrieval returned invalid metadata at index=%s.", index)
            raise RetrievalError(RETRIEVAL_REASON)
        valid_rows.append(row)
    return valid_rows


def build_context(chunks: list[dict[str, Any]]) -> str:
    """Render retrieved chunks as grounded context for the generation prompt.

    Only metadata stored on document_chunk is used: the current data model keeps no page
    numbers, so sources are identified by document filename and chunk index only.
    """
    blocks = [
        f"[Source {index}]\n"
        f"Document: {chunk['filename']}\n"
        f"Chunk: {chunk['chunk_index']}\n\n"
        f"{chunk['content']}"
        for index, chunk in enumerate(chunks, start=1)
    ]
    return "\n\n".join(blocks)
