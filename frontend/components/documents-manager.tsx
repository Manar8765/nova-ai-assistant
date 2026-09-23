"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { createClient } from "../lib/supabase/client";

type DocumentRecord = {
  document_id: string;
  filename: string;
  file_type: "PDF" | "TXT" | "DOCX";
  file_size: number;
  status: "UPLOADED" | "PROCESSING" | "READY" | "FAILED";
  failure_reason: string | null;
};

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function formatFileSize(fileSize: number) {
  return `${(fileSize / 1024 / 1024).toFixed(2)} MB`;
}

export function DocumentsManager() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);

  const getAccessToken = useCallback(async () => {
    const {
      data: { session },
    } = await createClient().auth.getSession();

    if (!session?.access_token) {
      throw new Error("Your session has expired. Please sign in again.");
    }

    return session.access_token;
  }, []);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setError("");

    try {
      const accessToken = await getAccessToken();
      const response = await fetch(`${apiUrl}/documents`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const body = await response.json();

      if (!response.ok) {
        throw new Error(body.detail ?? "Unable to load documents.");
      }

      setDocuments(body);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load documents.");
    } finally {
      setIsLoading(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get("file");

    if (!(file instanceof File) || file.size === 0) {
      setError("Choose a document to upload.");
      return;
    }

    setError("");
    setMessage("");
    setIsUploading(true);

    try {
      const accessToken = await getAccessToken();
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`${apiUrl}/documents`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: formData,
      });
      const body = await response.json();

      if (!response.ok) {
        throw new Error(body.detail ?? "Unable to upload document.");
      }

      form.reset();
      setMessage("Document uploaded.");
      await loadDocuments();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Unable to upload document.");
    } finally {
      setIsUploading(false);
    }
  }

  async function replaceDocument(documentId: string, event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setError("");
    setMessage("");
    setActiveDocumentId(documentId);

    try {
      const accessToken = await getAccessToken();
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`${apiUrl}/documents/${documentId}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: formData,
      });
      const body = await response.json();

      if (!response.ok) {
        throw new Error(body.detail ?? "Unable to replace document.");
      }

      event.target.value = "";
      setMessage("Document replaced.");
      await loadDocuments();
    } catch (replaceError) {
      setError(replaceError instanceof Error ? replaceError.message : "Unable to replace document.");
    } finally {
      setActiveDocumentId(null);
    }
  }

  async function deleteDocument(documentId: string, filename: string) {
    if (!window.confirm(`Delete ${filename}? This cannot be undone.`)) return;

    setError("");
    setMessage("");
    setActiveDocumentId(documentId);

    try {
      const accessToken = await getAccessToken();
      const response = await fetch(`${apiUrl}/documents/${documentId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` },
      });

      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail ?? "Unable to delete document.");
      }

      setMessage("Document deleted.");
      await loadDocuments();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete document.");
    } finally {
      setActiveDocumentId(null);
    }
  }

  return (
    <main>
      <p>
        <Link href="/dashboard">Back to dashboard</Link>
      </p>
      <h1>Documents</h1>

      <form onSubmit={uploadDocument}>
        <label htmlFor="document-file">Upload PDF, TXT, or DOCX (max 10 MB)</label>
        <input id="document-file" name="file" type="file" accept=".pdf,.txt,.docx" required />
        <button type="submit" disabled={isUploading}>
          {isUploading ? "Uploading..." : "Upload document"}
        </button>
      </form>

      {error ? <p role="alert">{error}</p> : null}
      {message ? <p role="status">{message}</p> : null}

      {isLoading ? <p>Loading documents...</p> : null}
      {!isLoading && documents.length === 0 ? <p>No documents have been uploaded yet.</p> : null}
      {!isLoading && documents.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th scope="col">Filename</th>
              <th scope="col">Type</th>
              <th scope="col">Size</th>
              <th scope="col">Status</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.document_id}>
                <td>{document.filename}</td>
                <td>{document.file_type}</td>
                <td>{formatFileSize(document.file_size)}</td>
                <td>
                  {document.status}
                  {document.status === "FAILED" && document.failure_reason ? (
                    <>
                      <br />
                      Reason: {document.failure_reason}
                    </>
                  ) : null}
                </td>
                <td>
                  <label htmlFor={`replace-${document.document_id}`}>Replace</label>{" "}
                  <input
                    id={`replace-${document.document_id}`}
                    type="file"
                    accept=".pdf,.txt,.docx"
                    disabled={activeDocumentId === document.document_id}
                    onChange={(event) => void replaceDocument(document.document_id, event)}
                  />{" "}
                  <button
                    type="button"
                    disabled={activeDocumentId === document.document_id}
                    onClick={() => void deleteDocument(document.document_id, document.filename)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </main>
  );
}
