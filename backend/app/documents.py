"""Tenant-scoped document metadata and storage endpoints."""

from __future__ import annotations

import os
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from supabase import Client, create_client
import truststore

truststore.inject_into_ssl()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

router = APIRouter(prefix="/documents", tags=["documents"])

DOCUMENT_BUCKET = "documents"
MAX_FILE_SIZE = 10 * 1024 * 1024
SUPPORTED_TYPES = {
    ".pdf": ("PDF", "application/pdf"),
    ".txt": ("TXT", "text/plain"),
    ".docx": (
        "DOCX",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


@dataclass(frozen=True)
class AuthenticatedProfile:
    user_id: str
    company_id: str
    role: str


class DocumentOut(BaseModel):
    document_id: UUID
    company_id: UUID
    filename: str
    file_path: str
    file_type: Literal["PDF", "TXT", "DOCX"]
    file_size: int
    status: Literal["UPLOADED", "PROCESSING", "READY", "FAILED"]
    created_at: str
    updated_at: str


@lru_cache
def get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.")

    return create_client(supabase_url, service_role_key)


def validate_upload_metadata(filename: str | None, content: bytes) -> tuple[str, str, str]:
    safe_filename = Path((filename or "").replace("\\", "/")).name
    extension = Path(safe_filename).suffix.lower()

    if extension not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Upload a PDF, TXT, or DOCX file.",
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size must not exceed 10 MB.",
        )

    file_type, content_type = SUPPORTED_TYPES[extension]
    if not _content_matches_type(file_type, content):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content does not match the selected PDF, TXT, or DOCX type.",
        )

    return safe_filename, file_type, content_type


def _content_matches_type(file_type: str, content: bytes) -> bool:
    if file_type == "PDF":
        return content.startswith(b"%PDF-")

    if file_type == "TXT":
        if b"\x00" in content:
            return False
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

    if file_type == "DOCX":
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
        except (BadZipFile, OSError):
            return False
        return "[Content_Types].xml" in names and "word/document.xml" in names

    return False


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid Bearer token is required.",
        )

    return authorization.removeprefix("Bearer ").strip()


def get_authenticated_profile(
    authorization: Annotated[str | None, Header()] = None,
    client: Annotated[Client, Depends(get_supabase_client)] = None,
) -> AuthenticatedProfile:
    token = _extract_bearer_token(authorization)

    try:
        auth_response = client.auth.get_user(token)
        auth_user = auth_response.user
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or has expired.",
        ) from exc

    if auth_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or has expired.",
        )

    try:
        response = (
            client.table("user")
            .select("user_id, company_id, role")
            .eq("user_id", str(auth_user.id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to retrieve your application profile.",
        ) from exc

    profile = response.data
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your application profile has not been created.",
        )

    return AuthenticatedProfile(
        user_id=str(profile["user_id"]),
        company_id=str(profile["company_id"]),
        role=str(profile["role"]),
    )


def get_admin_profile(
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
) -> AuthenticatedProfile:
    if profile.role.lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company admin access is required.",
        )

    return profile


def _storage_path(company_id: str, document_id: UUID, filename: str) -> str:
    return f"documents/{company_id}/{document_id}/{filename}"


def _upload_file(client: Client, path: str, content: bytes, content_type: str, *, upsert: bool) -> None:
    try:
        client.storage.from_(DOCUMENT_BUCKET).upload(
            path=path,
            file=content,
            file_options={"content-type": content_type, "upsert": str(upsert).lower()},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to upload the file to document storage.",
        ) from exc


def _remove_file(client: Client, path: str) -> None:
    client.storage.from_(DOCUMENT_BUCKET).remove([path])


def _download_file(client: Client, path: str) -> bytes:
    try:
        return client.storage.from_(DOCUMENT_BUCKET).download(path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to retrieve the stored file.",
        ) from exc


def _content_type(file_type: str) -> str:
    return next(mime_type for type_name, mime_type in SUPPORTED_TYPES.values() if type_name == file_type)


def _get_document_or_404(client: Client, document_id: UUID, company_id: str) -> dict:
    try:
        response = (
            client.table("document")
            .select("*")
            .eq("document_id", str(document_id))
            .eq("company_id", company_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to retrieve the document.",
        ) from exc

    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    return response.data


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: Annotated[UploadFile, File(...)],
    profile: Annotated[AuthenticatedProfile, Depends(get_admin_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict:
    content = await file.read(MAX_FILE_SIZE + 1)
    filename, file_type, content_type = validate_upload_metadata(file.filename, content)
    document_id = uuid4()
    path = _storage_path(profile.company_id, document_id, filename)

    _upload_file(client, path, content, content_type, upsert=False)

    values = {
        "document_id": str(document_id),
        "company_id": profile.company_id,
        "filename": filename,
        "file_path": path,
        "file_type": file_type,
        "file_size": len(content),
        "status": "UPLOADED",
    }

    try:
        response = client.table("document").insert(values).execute()
    except Exception as exc:
        try:
            _remove_file(client, path)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save document metadata. Storage cleanup was attempted.",
        ) from exc

    return response.data[0]


@router.get("", response_model=list[DocumentOut])
def list_documents(
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> list[dict]:
    try:
        response = (
            client.table("document")
            .select("*")
            .eq("company_id", profile.company_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to retrieve documents.",
        ) from exc

    return response.data


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: UUID,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict:
    return _get_document_or_404(client, document_id, profile.company_id)


@router.put("/{document_id}", response_model=DocumentOut)
async def replace_document(
    document_id: UUID,
    file: Annotated[UploadFile, File(...)],
    profile: Annotated[AuthenticatedProfile, Depends(get_admin_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict:
    existing = _get_document_or_404(client, document_id, profile.company_id)
    content = await file.read(MAX_FILE_SIZE + 1)
    filename, file_type, content_type = validate_upload_metadata(file.filename, content)
    new_path = _storage_path(profile.company_id, document_id, filename)
    old_path = existing["file_path"]
    old_content = _download_file(client, old_path) if new_path == old_path else None

    _upload_file(client, new_path, content, content_type, upsert=True)

    values = {
        "filename": filename,
        "file_path": new_path,
        "file_type": file_type,
        "file_size": len(content),
        "status": "UPLOADED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = (
            client.table("document")
            .update(values)
            .eq("document_id", str(document_id))
            .eq("company_id", profile.company_id)
            .execute()
        )
    except Exception as exc:
        if new_path == old_path and old_content is not None:
            try:
                _upload_file(
                    client,
                    old_path,
                    old_content,
                    _content_type(existing["file_type"]),
                    upsert=True,
                )
            except HTTPException:
                pass
        else:
            try:
                _remove_file(client, new_path)
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update document metadata. Storage rollback was attempted.",
        ) from exc

    if new_path != old_path:
        try:
            _remove_file(client, old_path)
        except Exception:
            # The database now points at the new file; the old object is inaccessible to users.
            pass

    return response.data[0]


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_document(
    document_id: UUID,
    profile: Annotated[AuthenticatedProfile, Depends(get_admin_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> None:
    existing = _get_document_or_404(client, document_id, profile.company_id)
    original_content = _download_file(client, existing["file_path"])

    try:
        _remove_file(client, existing["file_path"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to delete the stored file.",
        ) from exc

    try:
        (
            client.table("document")
            .delete()
            .eq("document_id", str(document_id))
            .eq("company_id", profile.company_id)
            .execute()
        )
    except Exception as exc:
        try:
            _upload_file(
                client,
                existing["file_path"],
                original_content,
                _content_type(existing["file_type"]),
                upsert=True,
            )
        except HTTPException:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete document metadata. Storage restore was attempted.",
        ) from exc
