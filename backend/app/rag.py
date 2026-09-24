"""RAG query API: grounded answers over the authenticated company's knowledge base."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.documents import AuthenticatedProfile, get_authenticated_profile, get_supabase_client
from app.embeddings import EmbeddingError, format_embedding_for_database, generate_embedding
from app.generation import GenerationError, generate_answer, identify_supporting_sources
from app.retrieval import RetrievalError, build_context, retrieve_chunks

router = APIRouter(prefix="/rag", tags=["rag"])

# A question follows the document chunk budget (CHUNK_SIZE = 1000 characters) and stays well
# inside the embedding model input limit, so 1000 characters is the MVP ceiling.
MAX_QUESTION_LENGTH = 1000

# An unanswerable question is a normal result, not an infrastructure failure.
NO_ANSWER_MESSAGE = (
    "I couldn't find sufficient information in the company's knowledge base to answer "
    "this question."
)

EMPTY_QUESTION_REASON = "Please provide a question."
LONG_QUESTION_REASON = (
    f"Your question is too long. Please keep it under {MAX_QUESTION_LENGTH} characters."
)
EMBEDDING_REASON = (
    "We couldn't process your question due to a temporary system error. Please try again."
)


class RagQueryIn(BaseModel):
    question: str


class RagSourceOut(BaseModel):
    document_id: UUID
    chunk_id: UUID
    filename: str
    chunk_index: int
    similarity: float


class RagAnswerOut(BaseModel):
    answer: str
    sources: list[RagSourceOut]


def validate_question(question: str) -> str:
    """Trim and validate the question, or fail with a safe 400 response."""
    normalized = question.strip()

    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=EMPTY_QUESTION_REASON)

    if len(normalized) > MAX_QUESTION_LENGTH:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=LONG_QUESTION_REASON)

    return normalized


def _embed_question(question: str) -> str:
    try:
        return format_embedding_for_database(generate_embedding(question))
    except EmbeddingError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=EMBEDDING_REASON) from exc


def _sources_from_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return source metadata only; chunk content stays inside the RAG pipeline."""
    return [
        {
            "document_id": chunk["document_id"],
            "chunk_id": chunk["chunk_id"],
            "filename": chunk["filename"],
            "chunk_index": chunk["chunk_index"],
            "similarity": float(chunk["similarity"]),
        }
        for chunk in chunks
    ]


def answer_question(
    client: Client,
    company_id: str,
    question: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Answer a question from the company knowledge base.

    ``company_id`` always comes from the authenticated profile, never from the request.
    """
    normalized = validate_question(question)
    query_embedding = _embed_question(normalized)

    try:
        chunks = retrieve_chunks(client, company_id, query_embedding)
    except RetrievalError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.reason) from exc

    if not chunks:
        # Without relevant context the answer is deterministic, so Gemini is not called.
        return {"answer": NO_ANSWER_MESSAGE, "sources": []}

    try:
        context = build_context(chunks)
        answer = generate_answer(normalized, context, history)
        supporting_sources = identify_supporting_sources(normalized, answer, context, len(chunks))
    except GenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.reason) from exc

    if not supporting_sources:
        # The answer is not backed by any retrieved excerpt, so the knowledge base is
        # insufficient: report the safe no-answer response instead of exposing arbitrary
        # Top-K retrieval results as sources.
        return {"answer": NO_ANSWER_MESSAGE, "sources": []}

    evidence = [chunks[index - 1] for index in supporting_sources]
    return {"answer": answer, "sources": _sources_from_chunks(evidence)}


@router.post("/query", response_model=RagAnswerOut)
def query_knowledge_base(
    payload: RagQueryIn,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict[str, Any]:
    return answer_question(client, profile.company_id, payload.question)
