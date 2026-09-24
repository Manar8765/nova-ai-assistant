"use client";

import Link from "next/link";
import { useCallback, useRef, useState, type FormEvent } from "react";
import { createClient } from "../lib/supabase/client";

type ChatSource = {
  document_id: string;
  chunk_id: string;
  filename: string;
  chunk_index: number;
  similarity: number;
};

type ChatMessage = {
  role: "user" | "assistant";
  answer: string;
  sources: ChatSource[];
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
  if (!Array.isArray(value)) {
    return [];
  }

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

export function ChatAssistant() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState("");
  const [isAsking, setIsAsking] = useState(false);
  const inFlightRef = useRef(false);

  const getAccessToken = useCallback(async () => {
    const {
      data: { session },
    } = await createClient().auth.getSession();

    if (!session?.access_token) {
      throw userFacingError(SESSION_ERROR);
    }

    return session.access_token;
  }, []);

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;

    if (inFlightRef.current) {
      return;
    }

    const question = String(new FormData(form).get("question") ?? "").trim();

    if (!question) {
      setError(EMPTY_QUESTION_ERROR);
      return;
    }

    setError("");
    setIsAsking(true);
    inFlightRef.current = true;

    try {
      const accessToken = await getAccessToken();
      const response = await fetch(`${apiUrl}/rag/query`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ question })
      });

      if (response.status === 401) {
        throw userFacingError(SESSION_ERROR);
      }

      const body = await response.json().catch(() => null);

      if (!response.ok) {
        throw userFacingError(detailMessage(body, FALLBACK_ERROR));
      }

      const answer = body?.answer;
      if (typeof answer !== "string" || !answer.trim()) {
        throw userFacingError(UNEXPECTED_RESPONSE_ERROR);
      }

      setMessages((previous) => [
        ...previous,
        { role: "user", answer: question, sources: [] },
        { role: "assistant", answer, sources: normalizeSources(body?.sources) }
      ]);
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
    <main>
      <p>
        <Link href="/dashboard">Back to dashboard</Link>
      </p>
      <h1>AI Assistant</h1>

      {messages.length === 0 ? (
        <section aria-labelledby="chat-intro-heading">
          <h2 id="chat-intro-heading">Ask the Nova AI Assistant</h2>
          <p>
            Ask about the company&apos;s products, services, or policies and get
            answers based on the company knowledge base.
          </p>
        </section>
      ) : null}

      {messages.length > 0 ? (
        <ol className="chat-messages" aria-label="Conversation so far">
          {messages.map((message, index) => (
            <li key={index} data-role={message.role}>
              <p className="chat-message-role">
                {message.role === "user" ? "You" : "Assistant"}
              </p>
              <p className="chat-message-answer">{message.answer}</p>
              {message.role === "assistant" && message.sources.length > 0 ? (
                <div>
                  <p className="chat-sources-heading">Sources:</p>
                  <ul className="chat-sources">
                    {message.sources.map((source) => (
                      <li key={source.chunk_id}>{source.filename}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}

      {isAsking ? <p role="status">Finding an answer…</p> : null}
      {error ? <p role="alert">{error}</p> : null}

      <form className="chat-form" onSubmit={askQuestion}>
        <label htmlFor="chat-question">Your question</label>
        <textarea
          id="chat-question"
          name="question"
          rows={3}
          required
          disabled={isAsking}
          placeholder="e.g. How many days do I have to return a product?"
        />
        <button type="submit" disabled={isAsking}>
          {isAsking ? "Sending…" : "Send"}
        </button>
      </form>
    </main>
  );
}