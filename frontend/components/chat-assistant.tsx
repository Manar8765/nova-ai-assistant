"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { createClient } from "../lib/supabase/client";
import { AppShell, Icon } from "./app-shell";

type ChatSource = {
  document_id: string;
  chunk_id: string;
  filename: string;
  chunk_index: number;
  similarity: number;
};

type ChatMessage = {
  message_id?: string;
  role: "user" | "assistant";
  content: string;
  sources: ChatSource[];
  message_index?: number;
};

type Conversation = {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

type UserFacingError = Error & { userFacing: boolean };

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const EMPTY_QUESTION_ERROR = "Please enter a question.";
const SESSION_ERROR = "Your session has expired. Please sign in again.";
const FALLBACK_ERROR = "We couldn't get an answer right now. Please try again.";
const UNEXPECTED_RESPONSE_ERROR = "We received an unexpected response. Please try again.";

function userFacingError(message: string): UserFacingError {
  return Object.assign(new Error(message), { userFacing: true });
}

function detailMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

function normalizeSources(value: unknown): ChatSource[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is ChatSource =>
      typeof entry === "object" &&
      entry !== null &&
      typeof entry.document_id === "string" &&
      typeof entry.chunk_id === "string" &&
      typeof entry.filename === "string" &&
      typeof entry.chunk_index === "number" &&
      typeof entry.similarity === "number"
  );
}

function normalizeConversations(value: unknown): Conversation[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is Conversation =>
      typeof entry === "object" &&
      entry !== null &&
      typeof entry.conversation_id === "string" &&
      typeof entry.title === "string" &&
      typeof entry.created_at === "string" &&
      typeof entry.updated_at === "string"
  );
}

function normalizeMessages(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (entry): entry is Record<string, unknown> =>
        typeof entry === "object" &&
        entry !== null &&
        (entry.role === "user" || entry.role === "assistant") &&
        typeof entry.content === "string"
    )
    .map((entry) => ({
      message_id: typeof entry.message_id === "string" ? entry.message_id : undefined,
      role: entry.role as "user" | "assistant",
      content: entry.content as string,
      sources: normalizeSources(entry.sources),
      message_index: typeof entry.message_index === "number" ? entry.message_index : undefined
    }));
}

export function ChatAssistant({ initialConversationId }: { initialConversationId?: string }) {
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState(initialConversationId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isAsking, setIsAsking] = useState(false);
  const inFlightRef = useRef(false);

  const getAccessToken = useCallback(async () => {
    const {
      data: { session }
    } = await createClient().auth.getSession();
    if (!session?.access_token) throw userFacingError(SESSION_ERROR);
    return session.access_token;
  }, []);

  const request = useCallback(
    async (path: string, options?: RequestInit) => {
      const accessToken = await getAccessToken();
      const response = await fetch(`${apiUrl}${path}`, {
        ...options,
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
          ...(options?.headers ?? {})
        }
      });
      if (response.status === 401) throw userFacingError(SESSION_ERROR);
      const body = await response.json().catch(() => null);
      if (!response.ok) throw userFacingError(detailMessage(body, FALLBACK_ERROR));
      return body;
    },
    [getAccessToken]
  );

  const loadConversations = useCallback(async () => {
    const body = await request("/conversations");
    const loaded = normalizeConversations(body);
    setConversations(loaded);
    return loaded;
  }, [request]);

  const loadMessages = useCallback(
    async (id: string) => {
      const body = await request(`/conversations/${encodeURIComponent(id)}/messages`);
      const loaded = normalizeMessages(body);
      setMessages(loaded);
    },
    [request]
  );

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError("");
    (async () => {
      try {
        const loaded = await loadConversations();
        if (cancelled) return;
        if (initialConversationId) {
          await loadMessages(initialConversationId);
        } else {
          setMessages([]);
        }
        if (!cancelled && initialConversationId && !loaded.some((item) => item.conversation_id === initialConversationId)) {
          throw userFacingError(FALLBACK_ERROR);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error && "userFacing" in loadError && loadError.userFacing
              ? loadError.message
              : FALLBACK_ERROR
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialConversationId, loadConversations, loadMessages]);

  function startNewConversation() {
    setConversationId(undefined);
    setMessages([]);
    setError("");
    router.push("/chat");
  }

  async function deleteConversation(id: string) {
    try {
      await request(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
      const remaining = conversations.filter((item) => item.conversation_id !== id);
      setConversations(remaining);
      if (conversationId === id) startNewConversation();
    } catch (deleteError) {
      setError(
        deleteError instanceof Error && "userFacing" in deleteError && deleteError.userFacing
          ? deleteError.message
          : FALLBACK_ERROR
      );
    }
  }

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (inFlightRef.current) return;
    const question = String(new FormData(form).get("question") ?? "").trim();
    if (!question) {
      setError(EMPTY_QUESTION_ERROR);
      return;
    }

    setError("");
    setIsAsking(true);
    inFlightRef.current = true;
    try {
      let activeId = conversationId;
      if (!activeId) {
        const created = await request("/conversations", {
          method: "POST",
          body: JSON.stringify({})
        });
        activeId = created?.conversation_id;
        if (typeof activeId !== "string") throw userFacingError(UNEXPECTED_RESPONSE_ERROR);
        setConversationId(activeId);
        router.push(`/chat/${activeId}`);
      }

      const body = await request(`/conversations/${encodeURIComponent(activeId)}/messages`, {
        method: "POST",
        body: JSON.stringify({ question })
      });
      const answer = body?.answer;
      if (typeof answer !== "string" || !answer.trim()) throw userFacingError(UNEXPECTED_RESPONSE_ERROR);
      await loadMessages(activeId);
      await loadConversations();
      form.reset();
    } catch (askError) {
      setError(
        askError instanceof Error && "userFacing" in askError && askError.userFacing
          ? askError.message
          : FALLBACK_ERROR
      );
    } finally {
      setIsAsking(false);
      inFlightRef.current = false;
    }
  }

  return (
    <AppShell title="AI Assistant" description="Ask questions grounded in your company knowledge base.">
      <div className="chat-layout">
        <aside className="conversation-sidebar" aria-label="Conversation history">
          <div className="conversation-sidebar-heading"><div><h2>Conversations</h2><p>{conversations.length ? `${conversations.length} saved` : "Your saved chats"}</p></div><button aria-label="New conversation" className="button button-primary button-small" type="button" onClick={startNewConversation} disabled={isAsking}><Icon name="plus" /> New chat</button></div>
          {conversations.length > 0 ? (
            <ul className="conversation-list">
              {conversations.map((conversation) => (
                <li className={conversationId === conversation.conversation_id ? "active" : ""} key={conversation.conversation_id}>
                  <Link href={`/chat/${conversation.conversation_id}`}><span className="conversation-list-icon"><Icon name="spark" /></span><span className="conversation-title">{conversation.title}</span></Link>
                  <button className="icon-button danger" aria-label={`Delete ${conversation.title}`} type="button" onClick={() => deleteConversation(conversation.conversation_id)} disabled={isAsking}><Icon name="trash" /></button>
                </li>
              ))}
            </ul>
          ) : <div className="empty-state compact"><div className="empty-icon"><Icon name="spark" /></div><p>No conversations yet.</p></div>}
        </aside>
        <section className="chat-panel" aria-labelledby="chat-heading">
          <div className="chat-panel-header"><div className="assistant-avatar"><Icon name="spark" /></div><div><h2 id="chat-heading">Nova AI Assistant</h2><p><span className="status-dot" /> Grounded in your knowledge base</p></div></div>
          <div className="chat-body">
          {isLoading ? <div className="loading-state" role="status"><span className="spinner" /> Loading conversation…</div> : null}
          {!isLoading && messages.length === 0 ? (
            <div className="chat-empty"><div className="hero-orb"><Icon name="spark" /></div><h3>How can Nova help?</h3><p>Ask about your company&apos;s products, services, or policies. Nova answers using only your approved knowledge base.</p><div className="prompt-grid"><button type="button" onClick={(event) => { const form = event.currentTarget.closest(".chat-panel")?.querySelector("textarea"); if (form instanceof HTMLTextAreaElement) { form.value = "What are our return and refund policies?"; form.focus(); } }}>What are our return policies?</button><button type="button" onClick={(event) => { const form = event.currentTarget.closest(".chat-panel")?.querySelector("textarea"); if (form instanceof HTMLTextAreaElement) { form.value = "What services does our company offer?"; form.focus(); } }}>What services do we offer?</button></div></div>
          ) : null}
          {messages.length > 0 ? (
            <ol className="chat-messages" aria-label="Conversation so far">
              {messages.map((message, index) => (
                <li key={message.message_id ?? `${message.message_index ?? index}-${message.role}`} data-role={message.role}>
                  <div className="message-avatar">{message.role === "user" ? "You" : <Icon name="spark" />}</div><div className="message-content"><p className="chat-message-role">{message.role === "user" ? "You" : "Nova"}</p><p className="chat-message-answer">{message.content}</p>
                  {message.role === "assistant" && message.sources.length > 0 ? (
                    <div className="sources-block">
                      <p className="chat-sources-heading"><span>Sources:</span> Knowledge references</p>
                      <ul className="chat-sources">
                        {message.sources.map((source) => <li key={source.chunk_id}><span className="source-file-icon"><Icon name="file" /></span><span><strong>{source.filename}</strong><small>Chunk {source.chunk_index + 1} · Relevant source</small></span></li>)}
                      </ul>
                    </div>
                  ) : null}
                  </div>
                </li>
              ))}
            </ol>
          ) : null}
          {isAsking ? <div className="typing-indicator" role="status"><span /><span /><span /> Nova is thinking</div> : null}
          {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
          </div>
          <form className="chat-form" onSubmit={askQuestion}>
            <label htmlFor="chat-question">Your question</label>
            <textarea id="chat-question" name="question" rows={2} required disabled={isAsking} placeholder="Ask Nova anything about your company..." />
            <button type="submit" className="button button-primary" disabled={isAsking}>{isAsking ? "Sending…" : "Send"} <Icon name="arrow" /></button>
          </form>
        </section>
      </div>
    </AppShell>
  );
}
