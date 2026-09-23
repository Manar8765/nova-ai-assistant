from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.documents import (
    AuthenticatedProfile,
    MAX_FILE_SIZE,
    _extract_bearer_token,
    _storage_path,
    get_admin_profile,
    get_supabase_client,
    validate_upload_metadata,
)
from app.main import app


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeDocumentTable:
    def __init__(self):
        self.values = None
        self.records = []
        self.filters = {}
        self.operation = "insert"

    def select(self, _columns):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = str(value)
        return self

    def maybe_single(self):
        return self

    def insert(self, values):
        self.operation = "insert"
        self.values = values
        return self

    def execute(self):
        if self.operation == "select":
            for record in self.records:
                if all(str(record.get(key)) == value for key, value in self.filters.items()):
                    return FakeResponse(record)
            return None
        self.records.append(self.values)
        return FakeResponse(
            [
                {
                    **self.values,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )


class FakeBucket:
    def __init__(self):
        self.uploads = []

    def upload(self, *, path, file, file_options):
        self.uploads.append((path, file, file_options))


class FakeStorage:
    def __init__(self):
        self.bucket = FakeBucket()

    def from_(self, name):
        assert name == "documents"
        return self.bucket


class FakeClient:
    def __init__(self):
        self.storage = FakeStorage()
        self.document_table = FakeDocumentTable()

    def table(self, name):
        assert name == "document"
        return self.document_table


@pytest.fixture
def client(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr("app.documents.process_document", lambda *args: None)
    app.dependency_overrides[get_supabase_client] = lambda: fake_client
    app.dependency_overrides[get_admin_profile] = lambda: AuthenticatedProfile(
        user_id="11111111-1111-1111-1111-111111111111",
        company_id="22222222-2222-2222-2222-222222222222",
        role="admin",
    )

    with TestClient(app) as test_client:
        yield test_client, fake_client

    app.dependency_overrides.clear()


def test_validate_upload_metadata_rejects_unsupported_file_type():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload_metadata("document.exe", b"content")

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "Unsupported file type. Please upload PDF, TXT, or DOCX."


def test_validate_upload_metadata_rejects_files_larger_than_ten_megabytes():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload_metadata("document.pdf", b"%PDF-1.7" + b"x" * MAX_FILE_SIZE)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "File is too large. Maximum size is 10 MB."


@pytest.mark.parametrize(
    ("filename", "content", "expected_type", "expected_content_type"),
    [
        ("plan.pdf", b"%PDF-1.7\n", "PDF", "application/pdf"),
        ("notes.TXT", b"Plain UTF-8 text", "TXT", "text/plain"),
        (
            "proposal.docx",
            None,
            "DOCX",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_validate_upload_metadata_accepts_supported_file_types(
    filename, content, expected_type, expected_content_type
):
    if content is None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<w:document />")
        content = buffer.getvalue()

    safe_filename, file_type, content_type = validate_upload_metadata(filename, content)

    assert safe_filename == filename
    assert file_type == expected_type
    assert content_type == expected_content_type


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("document.pdf", b"not a PDF"),
        ("notes.txt", b"\xff\xfe\x00"),
        ("proposal.docx", b"not a DOCX archive"),
    ],
)
def test_validate_upload_metadata_rejects_content_that_does_not_match_extension(filename, content):
    with pytest.raises(HTTPException) as exc_info:
        validate_upload_metadata(filename, content)

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == "The document appears to be corrupted or unreadable."


def test_storage_path_is_company_and_document_scoped():
    document_id = "33333333-3333-3333-3333-333333333333"

    assert _storage_path("22222222-2222-2222-2222-222222222222", document_id, "plan.pdf") == (
        "documents/22222222-2222-2222-2222-222222222222/"
        "33333333-3333-3333-3333-333333333333/33333333-3333-3333-3333-333333333333.pdf"
    )


@pytest.mark.parametrize(
    ("filename", "expected_extension"),
    [
        ("عقد إيجار.docx", ".docx"),
        ("ملف عربي مع مسافات.txt", ".txt"),
        ("plan.pdf", ".pdf"),
        ("company policy (2026).pdf", ".pdf"),
    ],
)
def test_storage_path_uses_an_ascii_safe_document_id_and_preserves_extension(filename, expected_extension):
    document_id = "33333333-3333-3333-3333-333333333333"

    path = _storage_path("22222222-2222-2222-2222-222222222222", document_id, filename)

    assert path.endswith(f"/{document_id}{expected_extension}")
    assert filename not in path
    assert path.isascii()


def test_non_admin_profiles_cannot_mutate_documents():
    with pytest.raises(HTTPException) as exc_info:
        get_admin_profile(
            AuthenticatedProfile(
                user_id="11111111-1111-1111-1111-111111111111",
                company_id="22222222-2222-2222-2222-222222222222",
                role="member",
            )
        )

    assert exc_info.value.status_code == 403


def test_bearer_token_is_required():
    with pytest.raises(HTTPException) as exc_info:
        _extract_bearer_token(None)

    assert exc_info.value.status_code == 401


def test_cors_allows_only_the_local_frontend_with_credentials():
    test_client = TestClient(app)
    response = test_client.options(
        "/documents",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"

    blocked_response = test_client.options(
        "/documents",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert blocked_response.status_code == 400
    assert "access-control-allow-origin" not in blocked_response.headers


def test_create_document_uploads_to_the_authenticated_users_company_path(client):
    test_client, fake_client = client

    response = test_client.post(
        "/documents",
        files={"file": ("plan.pdf", b"%PDF-1.7", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["company_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["filename"] == "plan.pdf"
    assert body["file_type"] == "PDF"
    assert body["status"] == "UPLOADED"
    assert fake_client.storage.bucket.uploads[0][0].startswith(
        "documents/22222222-2222-2222-2222-222222222222/"
    )


def test_duplicate_filename_is_rejected_for_the_same_company(client):
    test_client, fake_client = client
    fake_client.document_table.records.append(
        {"document_id": "33333333-3333-3333-3333-333333333333", "company_id": "22222222-2222-2222-2222-222222222222", "filename": "plan.pdf"}
    )

    response = test_client.post("/documents", files={"file": ("plan.pdf", b"%PDF-1.7", "application/pdf")})

    assert response.status_code == 409
    assert response.json()["detail"] == "A document with this filename already exists."
    assert fake_client.storage.bucket.uploads == []


def test_same_filename_is_allowed_for_a_different_company(client):
    test_client, fake_client = client
    fake_client.document_table.records.append(
        {"document_id": "33333333-3333-3333-3333-333333333333", "company_id": "other-company", "filename": "plan.pdf"}
    )

    response = test_client.post("/documents", files={"file": ("plan.pdf", b"%PDF-1.7", "application/pdf")})

    assert response.status_code == 201


def test_missing_filename_query_returns_none_and_allows_creation(client):
    test_client, fake_client = client

    response = test_client.post("/documents", files={"file": ("new-plan.pdf", b"%PDF-1.7", "application/pdf")})

    assert response.status_code == 201
    assert fake_client.document_table.records[0]["filename"] == "new-plan.pdf"


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("عقد إيجار.docx", None, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("ملف عربي مع مسافات.txt", "محتوى عربي".encode("utf-8"), "text/plain"),
        ("plan.pdf", b"%PDF-1.7", "application/pdf"),
        ("company policy (2026).pdf", b"%PDF-1.7", "application/pdf"),
    ],
)
def test_user_visible_filenames_are_preserved_while_storage_uses_ascii_safe_object_names(
    client, filename, content, content_type
):
    test_client, fake_client = client
    if content is None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<w:document />")
        content = buffer.getvalue()

    response = test_client.post("/documents", files={"file": (filename, content, content_type)})

    assert response.status_code == 201
    assert response.json()["filename"] == filename
    storage_path = fake_client.storage.bucket.uploads[0][0]
    assert response.json()["file_path"] == storage_path
    assert storage_path.isascii()
    assert storage_path.endswith(f".{Path(filename).suffix.lstrip('.').lower()}")
    assert filename not in storage_path


def test_document_failure_migration_preserves_existing_rows_and_rejects_future_duplicates():
    migration = Path(__file__).parents[2] / "supabase/migrations/20260923113000_add_document_failure_reason_and_unique_filename.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "add column failure_reason text" in sql
    assert "create trigger reject_duplicate_document_filename" in sql
    assert "before insert or update of company_id, filename" in sql
