"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { createClient } from "../lib/supabase/client";
import { AppShell, Icon } from "./app-shell";

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
    <AppShell title="Documents" description="Manage the files that power Nova's grounded answers.">
      <div className="page-actions">
        <form className="upload-form" onSubmit={uploadDocument}>
          <label className="button button-secondary upload-button" htmlFor="document-file">Choose file</label>
          <input className="visually-hidden" id="document-file" name="file" type="file" accept=".pdf,.txt,.docx" required />
          <button className="button button-primary upload-button" type="submit" disabled={isUploading}><Icon name="plus" /> {isUploading ? "Uploading..." : "Upload document"}</button>
          <span className="upload-hint">PDF, TXT, or DOCX · up to 10 MB</span>
        </form>
      </div>
      {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
      {message ? <div className="alert alert-success" role="status">{message}</div> : null}
      <div className="panel documents-panel">
        <div className="panel-heading"><div><h2>Knowledge base</h2><p>{documents.length} {documents.length === 1 ? "document" : "documents"} in your workspace</p></div><span className="table-caption">Updated just now</span></div>
        {isLoading ? <div className="loading-state"><span className="spinner" /> Loading documents...</div> : null}
        {!isLoading && documents.length === 0 ? <div className="empty-state"><div className="empty-icon"><Icon name="file" /></div><h3>Your knowledge base is empty</h3><p>Upload your first document to start asking Nova questions grounded in your company data.</p><label className="button button-secondary" htmlFor="document-file">Choose a document</label></div> : null}
        {!isLoading && documents.length > 0 ? (
          <div className="table-wrap"><table>
          <thead>
            <tr>
              <th scope="col">Document</th><th scope="col">Type</th><th scope="col">Size</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.document_id}>
                <td><div className="document-name"><span className="file-avatar">{document.file_type.slice(0, 3)}</span><strong>{document.filename}</strong></div></td>
                <td className="muted-cell">{document.file_type}</td><td className="muted-cell">{formatFileSize(document.file_size)}</td>
                <td><span className={`status-badge ${document.status.toLowerCase()}`}>{document.status.toLowerCase()}</span>
                  {document.status === "FAILED" && document.failure_reason ? (
                    <span className="failure-note">{document.failure_reason}</span>
                  ) : null}
                </td>
                <td className="row-actions">
                  <label className="button button-ghost button-small" htmlFor={`replace-${document.document_id}`}>Replace</label>
                  <input
                    className="visually-hidden"
                    id={`replace-${document.document_id}`}
                    type="file"
                    accept=".pdf,.txt,.docx"
                    disabled={activeDocumentId === document.document_id}
                    onChange={(event) => void replaceDocument(document.document_id, event)}
                  />
                  <button className="icon-button danger" aria-label={`Delete ${document.filename}`} type="button" disabled={activeDocumentId === document.document_id} onClick={() => void deleteDocument(document.document_id, document.filename)}><Icon name="trash" /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : null}
      </div>
    </AppShell>
  );
}
