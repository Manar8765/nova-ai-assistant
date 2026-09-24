from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from google.genai import types

from app import embeddings
from app import document_processing
from app.document_processing import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DocumentProcessingError,
    NO_TEXT_REASON,
    TEMPORARY_REASON,
    UTF8_REASON,
    chunk_text,
    embed_chunks,
    extract_docx_text,
    extract_pdf_text,
    extract_text,
    extract_txt_text,
    process_document,
)
from app.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EMBEDDING_REASON,
    EmbeddingError,
    format_embedding_for_database,
)


def _pdf_with_text(text: str) -> bytes:
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, object_data in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode())
        result.extend(object_data)
        result.extend(b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    result.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    result.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())
    return bytes(result)


def _docx_with_paragraphs(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_extracts_utf8_txt_and_rejects_binary_or_empty_text():
    assert extract_txt_text("First line\nSecond line".encode()) == "First line\nSecond line"

    with pytest.raises(DocumentProcessingError):
        extract_txt_text(b"binary\x00data")
    with pytest.raises(DocumentProcessingError):
        extract_text("TXT", b" \n\t ")


def test_extracts_arabic_utf8_txt_and_docx_content():
    assert extract_txt_text("محتوى عربي".encode("utf-8")) == "محتوى عربي"
    assert extract_docx_text(_docx_with_paragraphs("عقد إيجار", "نص عربي")) == "عقد إيجار\nنص عربي"


def test_extracts_text_from_a_valid_pdf_in_page_order():
    assert "PDF extraction works" in extract_pdf_text(_pdf_with_text("PDF extraction works"))


def test_extracts_readable_docx_paragraphs_in_order():
    assert extract_docx_text(_docx_with_paragraphs("First", "Second")) == "First\nSecond"


@pytest.mark.parametrize(
    ("file_type", "content"),
    [
        ("PDF", b"not a PDF"),
        ("DOCX", b"not a DOCX"),
        ("UNKNOWN", b"text"),
    ],
)
def test_invalid_processing_input_fails_cleanly(file_type, content):
    with pytest.raises(DocumentProcessingError):
        extract_text(file_type, content)


def test_chunking_is_deterministic_and_handles_short_and_large_text():
    short_text = "Short document"
    assert chunk_text(short_text) == [short_text]

    large_text = "a" * (CHUNK_SIZE * 2 + 100)
    chunks = chunk_text(large_text)
    assert chunks == chunk_text(large_text)
    assert len(chunks) == 3
    assert all(chunks)
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)
    assert chunks[0][-CHUNK_OVERLAP:] == chunks[1][:CHUNK_OVERLAP]
    assert chunks[1][-CHUNK_OVERLAP:] == chunks[2][:CHUNK_OVERLAP]


def _chunk_vector(text: str) -> list[float]:
    """Deterministic stand-in for a Gemini embedding so stored vectors can be compared."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round(digest[index % len(digest)] / 255, 6) for index in range(EMBEDDING_DIMENSIONS)]


class FakeEmbeddingModels:
    def __init__(self, vector_builder, failure_trigger):
        self.vector_builder = vector_builder
        self.failure_trigger = failure_trigger
        self.calls: list[str] = []

    def embed_content(self, *, model, contents, config):
        assert model == EMBEDDING_MODEL
        assert config.output_dimensionality == EMBEDDING_DIMENSIONS
        index = len(self.calls)
        self.calls.append(contents)
        failure = self.failure_trigger(index, contents) if self.failure_trigger else None
        if failure is not None:
            raise failure
        return types.EmbedContentResponse(
            embeddings=[types.ContentEmbedding(values=self.vector_builder(contents))]
        )


class FakeGeminiClient:
    def __init__(self, models):
        self.models = models


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch):
    """Embedding calls always go to a deterministic fake instead of the Gemini API."""

    def _install(vector_builder=_chunk_vector, failure_trigger=None):
        client = FakeGeminiClient(FakeEmbeddingModels(vector_builder, failure_trigger))
        monkeypatch.setattr(embeddings, "get_gemini_client", lambda: client)
        return client

    _install()
    return _install


class FakeResponse:
    def __init__(self, data=None):
        self.data = data


class FakeBucket:
    def __init__(self, objects):
        self.objects = objects

    def download(self, path):
        return self.objects[path]


class FakeStorage:
    def __init__(self, objects):
        self.bucket = FakeBucket(objects)

    def from_(self, name):
        assert name == "documents"
        return self.bucket


class FakeQuery:
    def __init__(self, client, table_name, operation):
        self.client = client
        self.table_name = table_name
        self.operation = operation
        self.filters = {}
        self.values = None

    def select(self, _columns):
        return self

    def maybe_single(self):
        return self

    def update(self, values):
        self.operation = "update"
        self.values = values
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        if self.table_name == "document":
            document = self.client.document
            if not all(str(document.get(key)) == str(value) for key, value in self.filters.items()):
                return FakeResponse(None)
            if self.operation == "select":
                return FakeResponse(document.copy())
            document.update(self.values)
            if "status" in self.values:
                self.client.status_history.append(document["status"])
            return FakeResponse([document.copy()])

        if self.operation == "delete":
            self.client.chunks = [
                chunk
                for chunk in self.client.chunks
                if not all(str(chunk.get(key)) == str(value) for key, value in self.filters.items())
            ]
        return FakeResponse([])


class FakeRpc:
    def __init__(self, client, params):
        self.client = client
        self.params = params

    def execute(self):
        document = self.client.document
        if self.params["p_document_id"] != document["document_id"] or self.params["p_company_id"] != document["company_id"]:
            return FakeResponse(False)

        if self.client.rpc_name == "begin_document_processing":
            if (
                document["file_path"] != self.params["p_file_path"]
                or document["processing_generation"] != self.params["p_processing_generation"]
            ):
                return FakeResponse(False)
            document.update({"status": "PROCESSING", "failure_reason": None})
            self.client.status_history.append("PROCESSING")
            self.client.chunks = []
            return FakeResponse(True)

        if (
            str(document["document_id"]) != self.params["p_document_id"]
            or str(document["company_id"]) != self.params["p_company_id"]
            or document["file_path"] != self.params["p_file_path"]
            or document["processing_generation"] != self.params["p_processing_generation"]
        ):
            return FakeResponse(False)
        # Mirrors the Phase 5 guard: a document may only be persisted with complete embeddings.
        if any(not chunk.get("embedding") for chunk in self.params["p_chunks"]):
            raise RuntimeError("Every document chunk must include an embedding")

        self.client.chunks = [
            chunk
            for chunk in self.client.chunks
            if chunk["document_id"] != self.params["p_document_id"]
        ]
        self.client.chunks.extend(
            {
                "document_id": self.params["p_document_id"],
                "company_id": self.params["p_company_id"],
                **chunk,
            }
            for chunk in self.params["p_chunks"]
        )
        return FakeResponse(True)


class FakeProcessingClient:
    def __init__(self, content=b"Current document text"):
        self.document = {
            "document_id": "document-1",
            "company_id": "company-1",
            "file_path": "documents/company-1/document-1/notes.txt",
            "file_type": "TXT",
            "status": "UPLOADED",
            "processing_generation": 0,
            "failure_reason": None,
        }
        self.chunks = []
        self.status_history = ["UPLOADED"]
        self.storage = FakeStorage({self.document["file_path"]: content})

    def table(self, table_name):
        return FakeQuery(self, table_name, "select")

    def rpc(self, name, params):
        assert name in {"begin_document_processing", "replace_document_chunks_for_generation"}
        self.rpc_name = name
        return FakeRpc(self, params)


def test_successful_processing_sets_ready_and_uses_ordered_indexes():
    client = FakeProcessingClient(b"A" * (CHUNK_SIZE + 100))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "READY"
    assert [chunk["chunk_index"] for chunk in client.chunks] == [0, 1]


def test_processing_failure_sets_failed_and_leaves_no_chunks():
    client = FakeProcessingClient(b" \n\t")
    client.chunks = [{"document_id": "document-1", "company_id": "company-1", "chunk_index": 0, "content": "old"}]

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == NO_TEXT_REASON
    assert client.chunks == []


def test_utf8_failure_uses_a_safe_reason_without_technical_details():
    client = FakeProcessingClient(b"\xff\xfe")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == UTF8_REASON
    assert "UnicodeDecodeError" not in client.document["failure_reason"]


def test_temporary_processing_exception_uses_safe_reason(monkeypatch):
    client = FakeProcessingClient()
    monkeypatch.setattr(client.storage.bucket, "download", lambda _path: (_ for _ in ()).throw(RuntimeError("secret URL")))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["failure_reason"] == TEMPORARY_REASON
    assert "secret URL" not in client.document["failure_reason"]


def test_transient_embedding_failure_retries_and_succeeds(monkeypatch):
    client = FakeProcessingClient(b"retryable text")
    calls = {"count": 0}
    def flaky_embedding(text):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary provider failure")
        return _chunk_vector(text)

    monkeypatch.setattr(document_processing, "generate_embedding", flaky_embedding)
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert calls["count"] == 2
    assert client.document["status"] == "READY"
    assert len(client.chunks) == 1


def test_permanent_embedding_shape_failure_is_not_retried(monkeypatch):
    client = FakeProcessingClient(b"invalid embedding")
    calls = {"count": 0}

    def invalid_embedding(_text):
        calls["count"] += 1
        raise EmbeddingError(EMBEDDING_REASON)

    monkeypatch.setattr(document_processing, "generate_embedding", invalid_embedding)
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert calls["count"] == 1
    assert client.document["status"] == "FAILED"
    assert client.chunks == []


def test_reprocessing_replaces_chunks_without_duplicates():
    client = FakeProcessingClient(b"repeatable text")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)
    first_chunks = client.chunks.copy()

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.chunks == first_chunks
    assert client.document["failure_reason"] is None


def test_updated_document_replaces_old_chunks_with_new_version_only():
    client = FakeProcessingClient(b"old content")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)
    client.document["file_path"] = "documents/company-1/document-1/replacement.txt"
    client.document["status"] = "UPLOADED"
    client.document["processing_generation"] = 1
    client.storage.bucket.objects[client.document["file_path"]] = b"new replacement content"

    process_document(client, "document-1", "company-1", client.document["file_path"], 1)

    assert client.document["status"] == "READY"
    assert [chunk["content"] for chunk in client.chunks] == ["new replacement content"]


def test_stale_processing_generation_cannot_write_chunks_or_ready_after_replacement():
    client = FakeProcessingClient(b"version A")
    generation_a = 0
    process_document(client, "document-1", "company-1", client.document["file_path"], generation_a)

    client.document.update({"status": "UPLOADED", "processing_generation": 1, "failure_reason": None})
    client.chunks = []
    client.storage.bucket.objects[client.document["file_path"]] = b"version B"

    process_document(client, "document-1", "company-1", client.document["file_path"], generation_a)

    assert client.document["status"] == "UPLOADED"
    assert client.chunks == []

    process_document(client, "document-1", "company-1", client.document["file_path"], 1)

    assert client.document["status"] == "READY"
    assert [chunk["content"] for chunk in client.chunks] == ["version B"]


def test_stale_or_cross_tenant_processing_cannot_access_chunks():
    client = FakeProcessingClient()
    client.chunks = [{"document_id": "document-1", "company_id": "company-1", "chunk_index": 0, "content": "existing"}]

    process_document(client, "document-1", "other-company", client.document["file_path"], 0)

    assert client.document["status"] == "UPLOADED"
    assert client.chunks[0]["content"] == "existing"


def test_chunk_migration_enforces_tenant_reads_and_cascade_delete():
    migration = Path(__file__).parents[2] / "supabase/migrations/20260923110000_create_document_chunks.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "references public.document(document_id, company_id) on delete cascade" in sql
    assert "profile.company_id = document_chunk.company_id" in sql
    assert "grant select on table public.document_chunk to authenticated" in sql
    assert "to service_role" in sql


def test_generation_migration_makes_replacement_metadata_and_chunk_invalidation_atomic():
    migration = Path(__file__).parents[2] / "supabase/migrations/20260923114000_add_document_processing_generation.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "add column processing_generation integer not null default 0" in sql
    assert "replace_document_metadata_and_clear_chunks" in sql
    assert "processing_generation = processing_generation + 1" in sql
    assert "delete from public.document_chunk" in sql
    assert "replace_document_chunks_for_generation" in sql


def test_replace_route_uses_atomic_metadata_and_chunk_invalidation_rpc():
    source = (Path(__file__).parents[1] / "app/documents.py").read_text(encoding="utf-8")

    assert "_replace_document_metadata_and_clear_chunks(" in source
    assert '"replace_document_metadata_and_clear_chunks"' in source
    assert "clear_document_chunks" not in source


def test_embed_chunks_pairs_every_chunk_with_its_own_embedding(fake_gemini):
    gemini = fake_gemini()

    embedded = embed_chunks(["first chunk", "second chunk"])

    assert [item["chunk_index"] for item in embedded] == [0, 1]
    assert [item["content"] for item in embedded] == ["first chunk", "second chunk"]
    assert gemini.models.calls == ["first chunk", "second chunk"]
    assert embedded[0]["embedding"] == format_embedding_for_database(_chunk_vector("first chunk"))
    assert embedded[1]["embedding"] == format_embedding_for_database(_chunk_vector("second chunk"))
    assert embedded[0]["embedding"] != embedded[1]["embedding"]


def test_embed_chunks_turns_a_provider_failure_into_a_safe_processing_error(fake_gemini):
    fake_gemini(failure_trigger=lambda _index, _text: RuntimeError("provider url https://internal.example"))

    with pytest.raises(DocumentProcessingError) as exc_info:
        embed_chunks(["chunk"])

    assert exc_info.value.reason == EMBEDDING_REASON
    assert "internal.example" not in exc_info.value.reason


def test_successful_processing_persists_one_embedding_per_chunk():
    client = FakeProcessingClient(b"A" * (CHUNK_SIZE + 100))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "READY"
    assert client.document["failure_reason"] is None
    assert client.status_history == ["UPLOADED", "PROCESSING", "READY"]
    assert [chunk["chunk_index"] for chunk in client.chunks] == [0, 1]
    for chunk in client.chunks:
        assert chunk["embedding"] == format_embedding_for_database(_chunk_vector(chunk["content"]))


def test_multiple_chunks_receive_their_own_distinct_embedding(fake_gemini):
    gemini = fake_gemini()
    content = "".join(f"section {index:02d} " + "z" * 60 + "\n" for index in range(40))
    client = FakeProcessingClient(content.encode("utf-8"))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "READY"
    assert len(client.chunks) >= 3
    assert gemini.models.calls == [chunk["content"] for chunk in client.chunks]
    stored_embeddings = [chunk["embedding"] for chunk in client.chunks]
    assert len(set(stored_embeddings)) == len(stored_embeddings)
    for chunk in client.chunks:
        assert chunk["embedding"] == format_embedding_for_database(_chunk_vector(chunk["content"]))


def test_one_transient_embedding_failure_retries_and_completes_without_duplicates(fake_gemini):
    gemini = fake_gemini(
        failure_trigger=lambda index, _text: RuntimeError("provider unavailable") if index == 2 else None
    )
    content = "".join(f"section {index:02d} " + "z" * 60 + "\n" for index in range(40))
    client = FakeProcessingClient(content.encode("utf-8"))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert len(gemini.models.calls) == 7
    assert client.document["status"] == "READY"
    assert client.document["failure_reason"] is None
    assert client.status_history == ["UPLOADED", "PROCESSING", "PROCESSING", "READY"]
    assert len(client.chunks) == len(set(chunk["chunk_index"] for chunk in client.chunks))


def test_transient_embedding_failure_is_bounded_and_marks_failed(fake_gemini):
    gemini = fake_gemini(
        failure_trigger=lambda _index, _text: RuntimeError("provider unavailable")
    )
    client = FakeProcessingClient(b"one chunk")

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert len(gemini.models.calls) == 3
    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == EMBEDDING_REASON
    assert client.chunks == []


def test_invalid_embedding_dimensions_fail_safely(fake_gemini):
    fake_gemini(vector_builder=lambda _text: _chunk_vector("short vector")[:512])
    client = FakeProcessingClient()

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == EMBEDDING_REASON
    assert "512" not in client.document["failure_reason"]
    assert client.chunks == []


def test_missing_api_key_fails_the_document_with_a_safe_reason(fake_gemini):
    fake_gemini(failure_trigger=lambda _index, _text: EmbeddingError(EMBEDDING_REASON))
    client = FakeProcessingClient()

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == EMBEDDING_REASON
    assert "GEMINI_API_KEY" not in client.document["failure_reason"]
    assert client.chunks == []


def test_embedding_network_failure_exposes_no_technical_details(fake_gemini):
    fake_gemini(failure_trigger=lambda _index, _text: ConnectionError("dial tcp 10.0.0.5:443 refused"))
    client = FakeProcessingClient()

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    reason = client.document["failure_reason"]
    assert client.document["status"] == "FAILED"
    assert reason == EMBEDDING_REASON
    assert "10.0.0.5" not in reason
    assert "ConnectionError" not in reason
    assert "443" not in reason


def test_arabic_chunk_text_is_embedded_normally(fake_gemini):
    gemini = fake_gemini()
    client = FakeProcessingClient(("محتوى عربي للاختبار " * 80).encode("utf-8"))

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "READY"
    assert len(client.chunks) >= 2
    assert all("محتوى عربي" in call for call in gemini.models.calls)
    for chunk in client.chunks:
        assert chunk["embedding"] == format_embedding_for_database(_chunk_vector(chunk["content"]))
        assert len(chunk["embedding"].strip("[]").split(",")) == EMBEDDING_DIMENSIONS


def test_replacement_persists_new_embeddings_without_mixing_old_ones():
    client = FakeProcessingClient(b"old content")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)
    old_embeddings = [chunk["embedding"] for chunk in client.chunks]
    assert old_embeddings

    client.document["file_path"] = "documents/company-1/document-1/replacement.txt"
    client.document.update({"status": "UPLOADED", "processing_generation": 1})
    client.storage.bucket.objects[client.document["file_path"]] = b"new replacement content"

    process_document(client, "document-1", "company-1", client.document["file_path"], 1)

    assert client.document["status"] == "READY"
    assert [chunk["content"] for chunk in client.chunks] == ["new replacement content"]
    new_embeddings = [chunk["embedding"] for chunk in client.chunks]
    assert new_embeddings == [format_embedding_for_database(_chunk_vector("new replacement content"))]
    assert set(new_embeddings).isdisjoint(old_embeddings)


def test_failed_replacement_leaves_no_stale_embeddings(fake_gemini):
    client = FakeProcessingClient(b"old content")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)
    assert [chunk["embedding"] for chunk in client.chunks]

    client.document["file_path"] = "documents/company-1/document-1/replacement.txt"
    client.document.update({"status": "UPLOADED", "processing_generation": 1})
    client.storage.bucket.objects[client.document["file_path"]] = b"new replacement content"
    fake_gemini(failure_trigger=lambda _index, _text: RuntimeError("provider unavailable"))

    process_document(client, "document-1", "company-1", client.document["file_path"], 1)

    assert client.document["status"] == "FAILED"
    assert client.document["failure_reason"] == EMBEDDING_REASON
    assert client.chunks == []


def test_stale_processing_cannot_write_embeddings_for_a_newer_generation():
    client = FakeProcessingClient(b"version A")
    process_document(client, "document-1", "company-1", client.document["file_path"], 0)
    assert [chunk["embedding"] for chunk in client.chunks]

    client.document.update({"status": "UPLOADED", "processing_generation": 1, "failure_reason": None})
    client.chunks = []
    client.storage.bucket.objects[client.document["file_path"]] = b"version B"

    process_document(client, "document-1", "company-1", client.document["file_path"], 0)

    assert client.document["status"] == "UPLOADED"
    assert client.chunks == []

    process_document(client, "document-1", "company-1", client.document["file_path"], 1)

    assert [chunk["content"] for chunk in client.chunks] == ["version B"]
    assert [chunk["embedding"] for chunk in client.chunks] == [
        format_embedding_for_database(_chunk_vector("version B"))
    ]


def test_embeddings_follow_the_same_tenant_isolation_as_chunks(fake_gemini):
    gemini = fake_gemini()
    existing = {
        "document_id": "document-1",
        "company_id": "company-1",
        "chunk_index": 0,
        "content": "existing",
        "embedding": format_embedding_for_database(_chunk_vector("existing")),
    }
    client = FakeProcessingClient()
    client.chunks = [existing]

    process_document(client, "document-1", "company-2", client.document["file_path"], 0)

    assert client.document["status"] == "UPLOADED"
    assert client.chunks == [existing]
    assert gemini.models.calls == []


def test_embedding_migration_adds_a_nullable_768_dimension_column_without_a_search_index():
    migration = Path(__file__).parents[2] / "supabase/migrations/20260923120000_add_document_chunk_embeddings.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "create extension if not exists vector" in sql
    assert "add column embedding vector(768)" in sql
    assert "embedding vector(768) not null" not in sql
    assert "embedding vector(768) default" not in sql
    assert "(chunk.value ->> 'embedding')::vector" in sql
    assert "every document chunk must include an embedding" in sql.lower()
    assert "replace_document_chunks_for_generation" in sql
    assert "processing_generation = p_processing_generation" in sql

    # Retrieval and its index belong to a later phase, and no existing column, policy, or
    # tenant-scoped read grant may be removed.
    assert "hnsw" not in sql.lower()
    assert "ivfflat" not in sql.lower()
    assert "drop column" not in sql.lower()
    assert "drop policy" not in sql.lower()
    assert "revoke select on table public.document_chunk" not in sql


def test_processing_generates_embeddings_before_the_document_becomes_ready():
    source = (Path(__file__).parents[1] / "app/document_processing.py").read_text(encoding="utf-8")

    assert "embed_chunks(chunks)" in source
    assert '"embedding": embedding' in source
    assert "replace_document_chunks_for_generation" in source
    assert source.index("embed_chunks(chunks)") < source.index('"READY"')
