"""Text extraction, deterministic chunking, embeddings, and document processing lifecycle."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from docx import Document
from pypdf import PdfReader
from supabase import Client

from app.embeddings import EmbeddingError, format_embedding_for_database, generate_embedding

DOCUMENT_BUCKET = "documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

logger = logging.getLogger(__name__)

CORRUPTED_REASON = "The document appears to be corrupted or unreadable."
NO_TEXT_REASON = "Unable to extract readable text from this document."
UTF8_REASON = "Unable to read this text file. Please save it as UTF-8 and try again."
TEMPORARY_REASON = "We couldn't process this document due to a temporary system error. Please try again."
MAX_PROCESSING_ATTEMPTS = 3


class DocumentProcessingError(ValueError):
    """Raised when a supported document cannot yield usable text."""

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def extract_pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise DocumentProcessingError(CORRUPTED_REASON) from exc


def extract_txt_text(content: bytes) -> str:
    if b"\x00" in content:
        raise DocumentProcessingError(UTF8_REASON)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentProcessingError(UTF8_REASON) from exc


def extract_docx_text(content: bytes) -> str:
    try:
        document = Document(BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:
        raise DocumentProcessingError(CORRUPTED_REASON) from exc


def extract_text(file_type: str, content: bytes) -> str:
    extractors = {
        "PDF": extract_pdf_text,
        "TXT": extract_txt_text,
        "DOCX": extract_docx_text,
    }
    try:
        extracted = extractors[file_type](content)
    except KeyError as exc:
        raise DocumentProcessingError(CORRUPTED_REASON) from exc

    if not extracted.strip():
        raise DocumentProcessingError(NO_TEXT_REASON)
    return extracted


def chunk_text(text: str, *, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller than chunk_size.")

    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = end - overlap

    return chunks


def embed_chunks(chunks: list[str]) -> list[dict[str, Any]]:
    """Pair every chunk with its own embedding before anything is persisted.

    READY means every chunk carries an embedding, so one failing embed aborts the whole
    batch and the document lifecycle stores a safe failure reason instead.
    """
    embedded: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        try:
            embedding = format_embedding_for_database(generate_embedding(chunk))
        except EmbeddingError as exc:
            logger.error("Embedding generation failed for chunk_index=%s.", index)
            raise DocumentProcessingError(exc.reason, retryable=exc.retryable) from exc
        embedded.append({"chunk_index": index, "content": chunk, "embedding": embedding})
    return embedded


def _get_document(client: Client, document_id: str, company_id: str) -> dict[str, Any] | None:
    response = (
        client.table("document")
        .select("document_id, company_id, file_path, file_type, processing_generation")
        .eq("document_id", document_id)
        .eq("company_id", company_id)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


def _set_status(
    client: Client,
    document_id: str,
    company_id: str,
    file_path: str,
    processing_generation: int,
    status: str,
    failure_reason: str | None = None,
) -> None:
    (
        client.table("document")
        .update({"status": status, "failure_reason": failure_reason})
        .eq("document_id", document_id)
        .eq("company_id", company_id)
        .eq("file_path", file_path)
        .eq("processing_generation", processing_generation)
        .execute()
    )


def _begin_processing(
    client: Client, document_id: str, company_id: str, file_path: str, processing_generation: int
) -> bool:
    response = client.rpc(
        "begin_document_processing",
        {
            "p_document_id": document_id,
            "p_company_id": company_id,
            "p_file_path": file_path,
            "p_processing_generation": processing_generation,
        },
    ).execute()
    return bool(response.data)


def _replace_chunks(
    client: Client,
    document_id: str,
    company_id: str,
    file_path: str,
    processing_generation: int,
    chunks: list[dict[str, Any]],
) -> bool:
    response = client.rpc(
        "replace_document_chunks_for_generation",
        {
            "p_document_id": document_id,
            "p_company_id": company_id,
            "p_file_path": file_path,
            "p_processing_generation": processing_generation,
            "p_chunks": chunks,
        },
    ).execute()
    return bool(response.data)


def process_document(
    client: Client, document_id: str, company_id: str, expected_file_path: str, processing_generation: int
) -> None:
    """Process one current document version, leaving it READY or FAILED.

    The expected path prevents an older background task from writing chunks after a
    replacement has stored a newer file for the same document ID.
    """
    for attempt in range(1, MAX_PROCESSING_ATTEMPTS + 1):
        try:
            document = _get_document(client, document_id, company_id)
            if (
                not document
                or document["file_path"] != expected_file_path
                or document["processing_generation"] != processing_generation
            ):
                return

            if not _begin_processing(client, document_id, company_id, expected_file_path, processing_generation):
                return
            content = client.storage.from_(DOCUMENT_BUCKET).download(expected_file_path)
            chunks = chunk_text(extract_text(document["file_type"], content))
            if not chunks:
                raise DocumentProcessingError(NO_TEXT_REASON)
            embedded_chunks = embed_chunks(chunks)
            if not _replace_chunks(
                client, document_id, company_id, expected_file_path, processing_generation, embedded_chunks
            ):
                return
            _set_status(client, document_id, company_id, expected_file_path, processing_generation, "READY")
            return
        except DocumentProcessingError as exc:
            logger.exception("Document processing failed for document_id=%s", document_id)
            if not exc.retryable or attempt >= MAX_PROCESSING_ATTEMPTS:
                _mark_processing_failed(
                    client, document_id, company_id, expected_file_path, processing_generation, exc.reason
                )
                return
            logger.warning(
                "Retryable document processing failure for document_id=%s; retrying attempt %s/%s.",
                document_id,
                attempt + 1,
                MAX_PROCESSING_ATTEMPTS,
                exc_info=True,
            )
        except Exception:
            if attempt < MAX_PROCESSING_ATTEMPTS:
                logger.warning(
                    "Transient document processing failure for document_id=%s; retrying attempt %s/%s.",
                    document_id,
                    attempt + 1,
                    MAX_PROCESSING_ATTEMPTS,
                    exc_info=True,
                )
                continue
            logger.exception("Document processing failed after retries for document_id=%s", document_id)
            _mark_processing_failed(
                client, document_id, company_id, expected_file_path, processing_generation, TEMPORARY_REASON
            )
            return


def _mark_processing_failed(
    client: Client,
    document_id: str,
    company_id: str,
    expected_file_path: str,
    processing_generation: int,
    reason: str,
) -> None:
    try:
        _set_status(client, document_id, company_id, expected_file_path, processing_generation, "FAILED", reason)
    except Exception:
        logger.exception("Unable to mark document_id=%s as FAILED", document_id)
