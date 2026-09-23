from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app.document_processing import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DocumentProcessingError,
    NO_TEXT_REASON,
    TEMPORARY_REASON,
    UTF8_REASON,
    chunk_text,
    extract_docx_text,
    extract_pdf_text,
    extract_text,
    extract_txt_text,
    process_document,
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
            self.client.chunks = []
            return FakeResponse(True)

        if (
            str(document["document_id"]) != self.params["p_document_id"]
            or str(document["company_id"]) != self.params["p_company_id"]
            or document["file_path"] != self.params["p_file_path"]
            or document["processing_generation"] != self.params["p_processing_generation"]
        ):
            return FakeResponse(False)
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
