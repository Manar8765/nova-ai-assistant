from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
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

    def insert(self, values):
        self.values = values
        return self

    def execute(self):
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
def client():
    fake_client = FakeClient()
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


def test_validate_upload_metadata_rejects_files_larger_than_ten_megabytes():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload_metadata("document.pdf", b"%PDF-1.7" + b"x" * MAX_FILE_SIZE)

    assert exc_info.value.status_code == 413


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


def test_storage_path_is_company_and_document_scoped():
    document_id = "33333333-3333-3333-3333-333333333333"

    assert _storage_path("22222222-2222-2222-2222-222222222222", document_id, "plan.pdf") == (
        "documents/22222222-2222-2222-2222-222222222222/"
        "33333333-3333-3333-3333-333333333333/plan.pdf"
    )


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
