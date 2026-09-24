from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app import embeddings, generation
from app.documents import AuthenticatedProfile, get_authenticated_profile, get_supabase_client
from app.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, format_embedding_for_database
from app.generation import (
    DEFAULT_GENERATION_MODEL,
    GENERATION_REASON,
    SOURCE_VERIFICATION_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    get_generation_model,
)
from app.main import app
from app.rag import (
    EMBEDDING_REASON,
    EMPTY_QUESTION_REASON,
    LONG_QUESTION_REASON,
    MAX_QUESTION_LENGTH,
    NO_ANSWER_MESSAGE,
    RagQueryIn,
    validate_question,
)
from app.retrieval import (
    RAG_SIMILARITY_THRESHOLD,
    RAG_TOP_K,
    RETRIEVAL_REASON,
    RetrievalError,
    build_context,
    retrieve_chunks,
)

COMPANY_A = "11111111-1111-1111-1111-111111111111"
COMPANY_B = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"
DOCUMENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CHUNK_A = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CHUNK_A2 = "cccccccc-cccc-cccc-cccc-cccccccccccc"

QUESTION = "How many days do I have to return a product?"
RETURN_POLICY_CONTENT = (
    "Customers can return products within 30 days.\n"
    "The original receipt is required for a return."
)
OTHER_COMPANY_CONTENT = "Company B internal policy: business class travel is allowed."
GENERATED_ANSWER = "You can return a product within 30 days with the original receipt."
RETURNED_COLUMNS = ("chunk_id", "document_id", "filename", "chunk_index", "content", "similarity")


def _vector(text: str) -> list[float]:
    """Deterministic stand-in for a Gemini embedding so tests can compare vectors."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round(digest[index % len(digest)] / 255, 6) for index in range(EMBEDDING_DIMENSIONS)]


class FakeGeminiModels:
    def __init__(self, *, embedding_failure=None):
        self.embedding_failure = embedding_failure
        self.embedding_calls: list[dict[str, Any]] = []

    def embed_content(self, *, model, contents, config):
        assert model == EMBEDDING_MODEL
        assert config.output_dimensionality == EMBEDDING_DIMENSIONS
        self.embedding_calls.append({"contents": contents})
        if self.embedding_failure is not None:
            raise self.embedding_failure
        return types.EmbedContentResponse(
            embeddings=[types.ContentEmbedding(values=_vector(contents))]
        )


class FakeGeminiClient:
    def __init__(self, models):
        self.models = models


class FakeGroqMessage:
    def __init__(self, content):
        self.content = content


class FakeGroqChoice:
    def __init__(self, content):
        self.message = FakeGroqMessage(content)


class FakeGroqCompletion:
    def __init__(self, content):
        self.choices = [FakeGroqChoice(content)]


class FakeGroqChatCompletions:
    """Records every grounded-generation and source-verification request."""

    def __init__(
        self,
        *,
        answer=GENERATED_ANSWER,
        failure=None,
        supporting_sources=None,
        verification_failure=None,
    ):
        self.answer = answer
        self.failure = failure
        self.supporting_sources = supporting_sources
        self.verification_failure = verification_failure
        self.generation_calls: list[dict[str, Any]] = []

    def create(self, *, model, messages, temperature, max_completion_tokens):
        roles = {message["role"]: message["content"] for message in messages}
        self.generation_calls.append(
            {
                "model": model,
                "system": roles.get("system"),
                "user": roles.get("user"),
                "temperature": temperature,
                "max_completion_tokens": max_completion_tokens,
                "messages": messages,
            }
        )

        if roles.get("system") == SOURCE_VERIFICATION_INSTRUCTION:
            if self.verification_failure is not None:
                raise self.verification_failure
            return FakeGroqCompletion(json.dumps(self._supporting_sources(roles.get("user", ""))))

        if self.failure is not None:
            raise self.failure
        return FakeGroqCompletion(self.answer)

    def _supporting_sources(self, verification_prompt: str) -> list[int]:
        if self.supporting_sources is not None:
            return list(self.supporting_sources)
        # Default treats every retrieved source as supportive, which mirrors the Phase 6
        # behaviour so retrieval-focused tests keep their full Top-K response.
        return list(range(1, verification_prompt.count("[Source ") + 1))


class FakeGroqChat:
    def __init__(self, completions):
        self.completions = completions


class FakeGroqClient:
    def __init__(self, completions):
        self.chat = FakeGroqChat(completions)


class FakeResponse:
    def __init__(self, data=None):
        self.data = data


class FakeAuthUser:
    def __init__(self, user_id=USER_ID):
        self.id = user_id


class FakeAuthResponse:
    def __init__(self, user):
        self.user = user


class FakeAuthUsers:
    def __init__(self, failure=None):
        self.failure = failure

    def get_user(self, _token):
        if self.failure is not None:
            raise self.failure
        return FakeAuthResponse(FakeAuthUser())


class FakeRpc:
    """Mirrors the match_document_chunks SQL so retrieval behaviour can be asserted."""

    def __init__(self, client, params):
        self.client = client
        self.params = params

    def execute(self):
        self.client.rpc_calls.append(self.params)

        if self.client.retrieval_failure is not None:
            raise self.client.retrieval_failure

        matching = [
            chunk
            for chunk in self.client.chunks
            if chunk["company_id"] == self.params["p_company_id"]
            and chunk["embedding"] is not None
            and chunk["document_status"] == "READY"
            and chunk["similarity"] >= self.params["p_similarity_threshold"]
        ]
        matching.sort(key=lambda chunk: chunk["similarity"], reverse=True)

        return FakeResponse(
            [
                {column: chunk[column] for column in RETURNED_COLUMNS}
                for chunk in matching[: self.params["p_match_count"]]
            ]
        )


class FakeRagClient:
    """Service-role Supabase stand-in exposing the retrieval RPC and the auth lookup."""

    def __init__(self, chunks=(), retrieval_failure=None, auth_failure=None):
        self.chunks = list(chunks)
        self.retrieval_failure = retrieval_failure
        self.rpc_calls: list[dict[str, Any]] = []
        self.auth = FakeAuthUsers(auth_failure)

    def rpc(self, name, params):
        assert name == "match_document_chunks"
        return FakeRpc(self, params)


def _chunk(
    content=RETURN_POLICY_CONTENT,
    *,
    company_id=COMPANY_A,
    chunk_id=CHUNK_A,
    document_id=DOCUMENT_A,
    filename="return-policy.txt",
    chunk_index=0,
    similarity=0.82,
    embedding="[0.1,0.2]",
    document_status="READY",
):
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "company_id": company_id,
        "filename": filename,
        "chunk_index": chunk_index,
        "content": content,
        "similarity": similarity,
        "embedding": embedding,
        "document_status": document_status,
    }


class RagHarness:
    def __init__(self, client, models, chat):
        self.client = client
        self.models = models
        self.chat = chat

    @property
    def embedding_calls(self):
        return self.models.embedding_calls

    @property
    def generation_calls(self):
        return self.chat.generation_calls


@pytest.fixture
def rag(monkeypatch):
    """Install fake Supabase, Gemini-embedding and Groq-generation clients for /rag/query."""

    def _install(
        *,
        chunks=(),
        retrieval_failure=None,
        answer=GENERATED_ANSWER,
        embedding_failure=None,
        generation_failure=None,
        supporting_sources=None,
        verification_failure=None,
    ):
        models = FakeGeminiModels(embedding_failure=embedding_failure)
        monkeypatch.setattr(embeddings, "get_gemini_client", lambda: FakeGeminiClient(models))
        chat = FakeGroqChatCompletions(
            answer=answer,
            failure=generation_failure,
            supporting_sources=supporting_sources,
            verification_failure=verification_failure,
        )
        monkeypatch.setattr(generation, "get_groq_client", lambda: FakeGroqClient(chat))
        client = FakeRagClient(chunks, retrieval_failure)
        app.dependency_overrides[get_supabase_client] = lambda: client
        app.dependency_overrides[get_authenticated_profile] = lambda: AuthenticatedProfile(
            user_id=USER_ID, company_id=COMPANY_A, role="admin"
        )
        return RagHarness(client, models, chat)

    yield _install
    app.dependency_overrides.clear()


def _query(question=QUESTION):
    with TestClient(app) as test_client:
        return test_client.post("/rag/query", json={"question": question})


def test_empty_question_is_rejected_with_a_safe_400(rag):
    rag()

    response = _query("   \n\t ")

    assert response.status_code == 400
    assert response.json()["detail"] == EMPTY_QUESTION_REASON


def test_over_long_question_is_rejected_with_a_safe_400(rag):
    rag()

    response = _query("a" * (MAX_QUESTION_LENGTH + 1))

    assert MAX_QUESTION_LENGTH == 1000
    assert response.status_code == 400
    assert response.json()["detail"] == LONG_QUESTION_REASON


def test_validate_question_trims_whitespace():
    assert validate_question(f"  {QUESTION}  ") == QUESTION


def test_rag_query_requires_authentication(monkeypatch):
    # The real authentication dependency runs; the request has no bearer token.
    app.dependency_overrides[get_supabase_client] = lambda: FakeRagClient()
    try:
        response = _query()
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "A valid Bearer token is required."


def test_rag_query_rejects_an_invalid_session(monkeypatch):
    app.dependency_overrides[get_supabase_client] = lambda: FakeRagClient(
        auth_failure=RuntimeError("token expired")
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/rag/query",
                json={"question": QUESTION},
                headers={"Authorization": "Bearer expired-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Your session is invalid or has expired."


def test_rag_request_never_accepts_a_company_id(rag):
    harness = rag(chunks=[_chunk()])

    with TestClient(app) as test_client:
        response = test_client.post("/rag/query", json={"question": QUESTION, "company_id": COMPANY_B})

    assert list(RagQueryIn.model_fields) == ["question"]
    assert harness.client.rpc_calls[0]["p_company_id"] == COMPANY_A
    assert [source["document_id"] for source in response.json()["sources"]] == [DOCUMENT_A]


def test_valid_question_is_embedded_with_gemini_embedding_2(rag):
    harness = rag(chunks=[_chunk()])

    response = _query(f"  {QUESTION}  ")

    assert response.status_code == 200
    assert harness.embedding_calls == [{"contents": QUESTION}]
    literal = harness.client.rpc_calls[0]["p_query_embedding"]
    assert literal == format_embedding_for_database(_vector(QUESTION))
    assert len(literal.strip("[]").split(",")) == 768


def test_query_embedding_failure_returns_a_safe_error(rag):
    harness = rag(embedding_failure=RuntimeError("429 quota exceeded with key secret-key"))

    response = _query()

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == EMBEDDING_REASON
    assert "secret-key" not in detail
    assert "quota" not in detail.lower()
    assert harness.client.rpc_calls == []
    assert harness.generation_calls == []


def test_relevant_chunks_are_retrieved_with_top_k_and_threshold_applied(rag):
    chunks = [
        _chunk(
            f"policy paragraph {index}",
            chunk_id=f"{index:08d}-0000-0000-0000-000000000000",
            chunk_index=index,
            similarity=0.95 - index / 100,
        )
        for index in range(7)
    ]
    harness = rag(chunks=chunks)

    response = _query()

    assert response.status_code == 200
    assert RAG_TOP_K == 5
    assert harness.client.rpc_calls[0]["p_match_count"] == RAG_TOP_K
    assert harness.client.rpc_calls[0]["p_similarity_threshold"] == RAG_SIMILARITY_THRESHOLD

    similarities = [source["similarity"] for source in response.json()["sources"]]
    assert len(similarities) == RAG_TOP_K
    assert similarities == sorted(similarities, reverse=True)


def test_chunks_below_the_similarity_threshold_are_not_retrieved(rag):
    harness = rag(
        chunks=[
            _chunk("Relevant return policy text.", similarity=RAG_SIMILARITY_THRESHOLD + 0.1),
            _chunk("Unrelated text.", chunk_id=CHUNK_A2, similarity=RAG_SIMILARITY_THRESHOLD - 0.1),
        ]
    )

    response = _query()

    assert [source["chunk_id"] for source in response.json()["sources"]] == [CHUNK_A]
    assert harness.generation_calls


def test_chunks_without_embeddings_are_ignored(rag):
    harness = rag(
        chunks=[
            _chunk("Embedded chunk.", similarity=0.8),
            _chunk("Legacy chunk without an embedding.", chunk_id=CHUNK_A2, similarity=0.99, embedding=None),
        ]
    )

    response = _query()

    assert [source["chunk_id"] for source in response.json()["sources"]] == [CHUNK_A]
    assert "Legacy chunk" not in harness.generation_calls[0]["user"]


@pytest.mark.parametrize("document_status", ["UPLOADED", "PROCESSING", "FAILED"])
def test_chunks_of_documents_that_are_not_ready_are_ignored(rag, document_status):
    harness = rag(
        chunks=[
            _chunk("Ready chunk.", similarity=0.8),
            _chunk(
                "Incomplete chunk.",
                chunk_id=CHUNK_A2,
                similarity=0.99,
                document_status=document_status,
            ),
        ]
    )

    response = _query()

    assert [source["chunk_id"] for source in response.json()["sources"]] == [CHUNK_A]
    assert "Incomplete chunk" not in harness.generation_calls[0]["user"]


@pytest.mark.parametrize("document_status", ["UPLOADED", "PROCESSING", "FAILED"])
def test_a_document_that_is_not_ready_alone_yields_no_answer_without_groq(rag, document_status):
    harness = rag(
        chunks=[_chunk("Incomplete chunk.", similarity=0.99, document_status=document_status)]
    )

    response = _query()

    assert response.json() == {"answer": NO_ANSWER_MESSAGE, "sources": []}
    assert harness.generation_calls == []


def test_company_a_cannot_retrieve_company_b_chunks(rag):
    harness = rag(
        chunks=[
            _chunk("Company A return policy text."),
            _chunk(
                OTHER_COMPANY_CONTENT,
                company_id=COMPANY_B,
                chunk_id=CHUNK_A2,
                document_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                filename="company-b-internal.txt",
                similarity=0.99,
            ),
        ]
    )

    response = _query("What is the travel policy?")

    assert harness.client.rpc_calls[0]["p_company_id"] == COMPANY_A
    assert [source["filename"] for source in response.json()["sources"]] == ["return-policy.txt"]

    prompt = harness.generation_calls[0]["user"]
    assert OTHER_COMPANY_CONTENT not in prompt
    assert "company-b-internal.txt" not in prompt
    assert COMPANY_B not in prompt

    # The source-verification call only ever sees this company's retrieved context.
    verification = harness.generation_calls[1]["user"]
    assert OTHER_COMPANY_CONTENT not in verification
    assert "company-b-internal.txt" not in verification
    assert COMPANY_B not in verification


def test_no_relevant_chunks_returns_an_answer_without_calling_groq(rag):
    harness = rag(chunks=[])

    response = _query("What is the employee vacation policy?")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_ANSWER_MESSAGE, "sources": []}
    assert harness.embedding_calls
    assert harness.generation_calls == []


def test_chunks_below_the_threshold_return_no_answer_without_calling_groq(rag):
    harness = rag(chunks=[_chunk("Unrelated text.", similarity=RAG_SIMILARITY_THRESHOLD - 0.05)])

    response = _query("What is the employee vacation policy?")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_ANSWER_MESSAGE, "sources": []}
    assert harness.generation_calls == []


def test_retrieval_failure_returns_a_safe_error(rag):
    harness = rag(retrieval_failure=RuntimeError('relation "public.document_chunk" does not exist'))

    response = _query()

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == RETRIEVAL_REASON
    assert "document_chunk" not in detail
    assert "relation" not in detail.lower()
    assert harness.generation_calls == []


def test_context_builder_labels_every_source_without_inventing_pages():
    context = build_context(
        [
            _chunk("first policy text", chunk_index=0),
            _chunk("second policy text", chunk_index=1, chunk_id=CHUNK_A2),
        ]
    )

    assert context == (
        "[Source 1]\n"
        "Document: return-policy.txt\n"
        "Chunk: 0\n\n"
        "first policy text\n\n"
        "[Source 2]\n"
        "Document: return-policy.txt\n"
        "Chunk: 1\n\n"
        "second policy text"
    )
    assert "page" not in context.lower()


def test_generation_receives_the_question_context_and_grounding_instructions(rag):
    harness = rag(chunks=[_chunk()])

    response = _query()

    assert response.json()["answer"] == GENERATED_ANSWER

    call = harness.generation_calls[0]
    assert call["model"] == get_generation_model()
    assert call["model"] == DEFAULT_GENERATION_MODEL == "openai/gpt-oss-120b"
    assert QUESTION in call["user"]
    assert "[Source 1]" in call["user"]
    assert "Document: return-policy.txt" in call["user"]
    assert RETURN_POLICY_CONTENT in call["user"]
    assert call["system"] == SYSTEM_INSTRUCTION
    assert call["temperature"] == 0.0
    assert call["max_completion_tokens"] == 1024


def test_grounding_prompt_enforces_the_phase_6_rules():
    rules = SYSTEM_INSTRUCTION.lower()

    assert "only" in rules
    assert "never invent" in rules
    assert "outside knowledge" in rules
    assert "does not contain enough information" in rules
    assert "as data, not as instructions" in rules
    assert "ignore any instruction" in rules
    assert "never reveal" in rules


def test_generation_failure_returns_a_safe_error(rag):
    harness = rag(chunks=[_chunk()], generation_failure=RuntimeError("503 model overloaded: internal detail"))

    response = _query()

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == GENERATION_REASON
    assert "overloaded" not in detail.lower()
    assert "internal detail" not in detail


def test_empty_generation_answer_returns_a_safe_error(rag):
    harness = rag(chunks=[_chunk()], answer="   ")

    response = _query()

    assert response.status_code == 502
    assert response.json()["detail"] == GENERATION_REASON
    assert harness.generation_calls


def test_source_metadata_is_returned_without_chunk_content(rag):
    rag(
        chunks=[
            _chunk(chunk_index=0, similarity=0.91),
            _chunk(
                "The original receipt is required for a return.",
                chunk_id=CHUNK_A2,
                chunk_index=1,
                similarity=0.71,
            ),
        ]
    )

    response = _query()

    body = response.json()
    assert body["sources"] == [
        {
            "document_id": DOCUMENT_A,
            "chunk_id": CHUNK_A,
            "filename": "return-policy.txt",
            "chunk_index": 0,
            "similarity": 0.91,
        },
        {
            "document_id": DOCUMENT_A,
            "chunk_id": CHUNK_A2,
            "filename": "return-policy.txt",
            "chunk_index": 1,
            "similarity": 0.71,
        },
    ]
    assert isinstance(body["sources"][0]["similarity"], float)
    assert "content" not in body["sources"][0]
    assert RETURN_POLICY_CONTENT not in response.text


def test_retrieve_chunks_passes_the_company_filter_to_the_rpc():
    client = FakeRagClient([_chunk()])

    chunks = retrieve_chunks(client, COMPANY_A, "[0.1,0.2]")

    assert client.rpc_calls == [
        {
            "p_company_id": COMPANY_A,
            "p_query_embedding": "[0.1,0.2]",
            "p_match_count": RAG_TOP_K,
            "p_similarity_threshold": RAG_SIMILARITY_THRESHOLD,
        }
    ]
    assert len(chunks) == 1


def test_retrieve_chunks_wraps_database_failures_in_a_safe_error():
    client = FakeRagClient(retrieval_failure=RuntimeError("permission denied for function match_document_chunks"))

    with pytest.raises(RetrievalError) as exc_info:
        retrieve_chunks(client, COMPANY_A, "[0.1,0.2]")

    assert exc_info.value.reason == RETRIEVAL_REASON
    assert "permission denied" not in exc_info.value.reason


def test_rag_migration_matches_the_phase_6_retrieval_contract():
    migration = (
        Path(__file__).parents[2]
        / "supabase/migrations/20260923130000_add_rag_match_document_chunks_function.sql"
    )
    sql = migration.read_text(encoding="utf-8")

    assert "create function public.match_document_chunks(" in sql
    assert "p_query_embedding::vector(768)" in sql
    assert "dc.embedding <=> v_query_embedding" in sql
    assert "dc.company_id = p_company_id" in sql
    assert "dc.embedding is not null" in sql
    assert "d.status = 'READY'" in sql
    assert "p_similarity_threshold" in sql
    assert "order by dc.embedding <=> v_query_embedding" in sql.lower()
    assert "limit p_match_count" in sql.lower()
    assert "security definer" in sql
    assert "set search_path = public, extensions" in sql
    assert "chunk_id uuid" in sql
    assert "filename varchar" in sql
    assert "similarity double precision" in sql
    assert (
        "grant execute on function public.match_document_chunks(uuid, text, integer, double precision) "
        "to service_role" in sql
    )
    assert "to authenticated" not in sql

    # Phase 6 keeps exact search and leaves the Phase 5 schema untouched.
    assert "hnsw" not in sql.lower()
    assert "ivfflat" not in sql.lower()
    assert "alter table" not in sql.lower()
    assert "drop " not in sql.lower()


def test_rag_endpoint_derives_the_company_from_the_authenticated_profile():
    source = (Path(__file__).parents[1] / "app/rag.py").read_text(encoding="utf-8")

    assert "Depends(get_authenticated_profile)" in source
    assert "profile.company_id" in source
    assert "retrieve_chunks(client, company_id, query_embedding)" in source
    # Chunks are never fetched in Python and filtered afterwards.
    assert 'table("document_chunk")' not in source
    assert "company_id" not in RagQueryIn.model_fields


def test_unknown_question_returns_the_safe_answer_with_no_sources(rag):
    # Threshold-passing noise is retrieved, but nothing supports the answer.
    harness = rag(
        chunks=[
            _chunk("Candidate CV summary for a sales role.", filename="cv.txt", similarity=0.72),
            _chunk(
                "Loose sentences from an unrelated corpus.",
                chunk_id=CHUNK_A2,
                filename="sentences.txt",
                chunk_index=1,
                similarity=0.61,
            ),
        ],
        answer="The company's knowledge base does not contain enough information to answer that question.",
        supporting_sources=[],
    )

    response = _query("What is the employee vacation policy?")

    assert response.status_code == 200
    assert response.json() == {"answer": NO_ANSWER_MESSAGE, "sources": []}
    assert "cv.txt" not in response.text
    assert "sentences.txt" not in response.text
    assert len(harness.generation_calls) == 2  # answer generation, then source verification


def test_known_question_returns_only_the_source_that_supports_the_answer(rag):
    harness = rag(
        chunks=[
            _chunk(RETURN_POLICY_CONTENT, filename="return-policy.txt", similarity=0.91),
            _chunk(
                "Scratch notes text.",
                chunk_id=CHUNK_A2,
                filename="New Text Document (2).txt",
                chunk_index=1,
                similarity=0.74,
            ),
            _chunk(
                "Loose sentences from an unrelated corpus.",
                chunk_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                filename="sentences.txt",
                chunk_index=2,
                similarity=0.68,
            ),
        ],
        supporting_sources=[1],
    )

    response = _query()

    body = response.json()
    assert body["answer"] == GENERATED_ANSWER
    assert [source["filename"] for source in body["sources"]] == ["return-policy.txt"]
    assert "New Text Document (2).txt" not in response.text
    assert "sentences.txt" not in response.text
    assert len(harness.generation_calls) == 2


def test_unrelated_retrieved_chunks_are_not_exposed_as_misleading_sources(rag):
    # The unrelated file ranks FIRST in retrieval but is not evidence for the answer.
    harness = rag(
        chunks=[
            _chunk("HR candidate CV notes.", filename="New Text Document (2).txt", similarity=0.95),
            _chunk(
                RETURN_POLICY_CONTENT,
                chunk_id=CHUNK_A2,
                filename="return-policy.txt",
                chunk_index=1,
                similarity=0.82,
            ),
        ],
        supporting_sources=[2],
    )

    response = _query()

    assert response.json()["answer"] == GENERATED_ANSWER
    assert [source["filename"] for source in response.json()["sources"]] == ["return-policy.txt"]
    assert "New Text Document (2).txt" not in response.text

    # Both rows were retrieved into the verification context; only the evidence survives.
    verification = harness.generation_calls[1]["user"]
    assert "New Text Document (2).txt" in verification
    assert "return-policy.txt" in verification


def test_source_verification_failure_returns_a_safe_error(rag):
    harness = rag(
        chunks=[_chunk()],
        verification_failure=RuntimeError("503 verifier unavailable: internal detail"),
    )

    response = _query()

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == GENERATION_REASON
    assert "verifier" not in detail.lower()
    assert "internal detail" not in detail
