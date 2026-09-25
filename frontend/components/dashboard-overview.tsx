"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { createClient } from "../lib/supabase/client";
import { AppShell, Icon } from "./app-shell";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type DocumentRecord = { document_id: string; filename: string; status: string; file_type: string };
type Conversation = { conversation_id: string; title: string; updated_at: string };

export function DashboardOverview({ userEmail, userName, companyName }: { userEmail?: string; userName?: string | null; companyName?: string | null }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [error, setError] = useState("");
  const getToken = useCallback(async () => {
    const { data: { session } } = await createClient().auth.getSession();
    if (!session?.access_token) throw new Error("Your session has expired. Please sign in again.");
    return session.access_token;
  }, []);
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const token = await getToken();
        const headers = { Authorization: `Bearer ${token}` };
        const [docsResponse, conversationsResponse] = await Promise.all([
          fetch(`${apiUrl}/documents`, { headers }),
          fetch(`${apiUrl}/conversations`, { headers }),
        ]);
        const docs = await docsResponse.json();
        const chats = await conversationsResponse.json();
        if (!docsResponse.ok || !conversationsResponse.ok) throw new Error("Unable to load workspace activity.");
        if (active) { setDocuments(Array.isArray(docs) ? docs : []); setConversations(Array.isArray(chats) ? chats : []); }
      } catch (loadError) { if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load workspace activity."); }
    })();
    return () => { active = false; };
  }, [getToken]);
  const ready = documents.filter((item) => item.status === "READY").length;
  const processing = documents.filter((item) => item.status === "PROCESSING").length;
  return <AppShell title={`Good morning${userName ? `, ${userName.split(" ")[0]}` : ""}`} description="A clear view of your knowledge workspace and AI activity." userName={userName} companyName={companyName}>
    {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
    <section className="summary-grid" aria-label="Workspace summary">
      <div className="summary-card"><div className="summary-icon blue"><Icon name="file" /></div><span>Total documents</span><strong>{documents.length}</strong><small>Knowledge assets</small></div>
      <div className="summary-card"><div className="summary-icon green"><Icon name="spark" /></div><span>Ready for AI</span><strong>{ready}</strong><small>{processing ? `${processing} processing` : "Up to date"}</small></div>
      <div className="summary-card"><div className="summary-icon violet"><Icon name="grid" /></div><span>Conversations</span><strong>{conversations.length}</strong><small>Saved sessions</small></div>
    </section>
    <section className="dashboard-grid">
      <div className="panel activity-panel"><div className="panel-heading"><div><h2>Recent documents</h2><p>Your latest knowledge assets</p></div><Link href="/documents" className="text-link">View all <Icon name="arrow" /></Link></div>
        {documents.length ? <div className="activity-list">{documents.slice(0, 5).map((doc) => <div className="activity-row" key={doc.document_id}><div className="file-avatar">{doc.file_type.slice(0, 3)}</div><div className="activity-copy"><strong>{doc.filename}</strong><span>{doc.file_type} document</span></div><span className={`status-badge ${doc.status.toLowerCase()}`}>{doc.status.toLowerCase()}</span></div>)}</div> : <div className="empty-state compact"><div className="empty-icon"><Icon name="file" /></div><h3>No documents yet</h3><p>Upload a document to give Nova knowledge.</p><Link href="/documents" className="button button-secondary">Upload document</Link></div>}
      </div>
      <div className="panel quick-panel"><div className="panel-heading"><div><h2>Quick actions</h2><p>Keep your workspace moving</p></div></div><Link href="/documents" className="quick-action"><span className="quick-icon blue"><Icon name="plus" /></span><span><strong>Upload document</strong><small>Add a PDF, TXT, or DOCX file</small></span><Icon name="arrow" /></Link><Link href="/chat" className="quick-action"><span className="quick-icon violet"><Icon name="spark" /></span><span><strong>Ask Nova</strong><small>Get a grounded answer from your KB</small></span><Icon name="arrow" /></Link></div>
    </section>
    <section className="panel conversation-preview"><div className="panel-heading"><div><h2>Recent conversations</h2><p>Pick up where you left off</p></div><Link href="/chat" className="text-link">Open assistant <Icon name="arrow" /></Link></div>{conversations.length ? <div className="conversation-preview-list">{conversations.slice(0, 4).map((conversation) => <Link href={`/chat/${conversation.conversation_id}`} className="conversation-preview-row" key={conversation.conversation_id}><span className="conversation-dot"><Icon name="spark" /></span><span>{conversation.title}</span><Icon name="arrow" /></Link>)}</div> : <p className="muted-copy">Your saved conversations will appear here.</p>}</section>
  </AppShell>;
}
