"""Phase 7 — customer chat frontend wired to the existing POST /rag/query endpoint."""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend"
MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"
CHAT_COMPONENT = FRONTEND / "components" / "chat-assistant.tsx"
CHAT_PAGE = FRONTEND / "app" / "chat" / "page.tsx"
DASHBOARD_PAGE = FRONTEND / "app" / "dashboard" / "page.tsx"
RAG_SOURCE = Path(__file__).parents[1] / "app" / "rag.py"


def _chat() -> str:
    return CHAT_COMPONENT.read_text(encoding="utf-8")


def _page() -> str:
    return CHAT_PAGE.read_text(encoding="utf-8")


def test_chat_shows_an_empty_state_with_helpful_guidance():
    source = _chat()

    assert "messages.length === 0" in source
    assert "products, services, or policies" in source
    assert "knowledge base" in source


def test_chat_provides_a_question_input_and_a_send_button():
    source = _chat()

    assert '<label htmlFor="chat-question">' in source
    assert "<textarea" in source
    assert 'name="question"' in source
    assert "onSubmit={askQuestion}" in source
    assert '<button type="submit"' in source
    assert source.count("disabled={isAsking}") >= 2


def test_chat_never_sends_an_empty_or_whitespace_only_question():
    source = _chat()

    assert ".trim()" in source
    assert "if (!question)" in source
    assert "setError(EMPTY_QUESTION_ERROR);" in source
    # The validation guard runs before any network call.
    assert source.index("if (!question)") < source.index("fetch(")


def test_chat_posts_the_question_to_the_rag_query_endpoint():
    source = _chat()

    assert 'const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"' in source
    assert "fetch(`${apiUrl}/rag/query`" in source
    assert 'method: "POST"' in source
    assert 'JSON.stringify({ question })' in source


def test_chat_sends_the_bearer_token_and_never_a_company_id():
    source = _chat()

    assert "createClient().auth.getSession()" in source
    assert "Authorization: `Bearer ${accessToken}`" in source
    assert '"Content-Type": "application/json"' in source
    assert "company_id" not in source


def test_chat_renders_the_user_question_and_the_ai_answer_in_order():
    source = _chat()

    assert source.index('{ role: "user", answer: question') < source.index(
        '{ role: "assistant", answer, sources: normalizeSources(body?.sources) }'
    )
    assert "data-role={message.role}" in source
    assert "{message.answer}" in source
    assert "!answer.trim()" in source


def test_chat_renders_sources_when_the_backend_returns_them():
    source = _chat()

    assert 'message.role === "assistant" && message.sources.length > 0' in source
    assert ">Sources:<" in source
    assert "{source.filename}" in source
    assert "key={source.chunk_id}" in source


def test_chat_never_fabricates_sources_when_none_are_returned():
    source = _chat()

    assert "if (!Array.isArray(value))" in source
    assert "sources: []" in source
    assert "entry is ChatSource" in source


def test_chat_shows_the_safe_no_answer_response_unchanged():
    rag = RAG_SOURCE.read_text(encoding="utf-8")

    assert "NO_ANSWER_MESSAGE" in rag
    assert '{"answer": NO_ANSWER_MESSAGE, "sources": []}' in rag

    source = _chat()
    # The backend answer text reaches the screen exactly as returned.
    assert "{message.answer}" in source
    for transformation in ("answer.slice", "answer.split", "answer.replace", "answer.substring"):
        assert transformation not in source


def test_chat_shows_loading_and_prevents_duplicate_submissions():
    source = _chat()

    assert 'role="status"' in source
    assert "if (inFlightRef.current)" in source
    assert source.index("if (inFlightRef.current)") < source.index("fetch(")
    assert "setIsAsking(true);" in source
    assert "setIsAsking(false);" in source
    assert source.count("disabled={isAsking}") >= 2


def test_chat_reports_only_safe_user_facing_errors():
    source = _chat()

    assert 'role="alert"' in source
    assert '"userFacing" in askError' in source
    assert "FALLBACK_ERROR" in source
    assert "UNEXPECTED_RESPONSE_ERROR" in source
    assert "response.status === 401" in source
    for technical in ("stack", "console.", "TypeError", "fetch failed", "SyntaxError"):
        assert technical not in source


def test_frontend_never_exposes_backend_secrets():
    allowed = {
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
    }

    for directory in ("app", "components", "lib"):
        for path in (FRONTEND / directory).rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".tsx"}:
                continue
            content = path.read_text(encoding="utf-8")
            for secret in ("GROQ_API_KEY", "GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "gsk_", "AIza"):
                assert secret not in content, path
            for name in set(re.findall(r"NEXT_PUBLIC_[A-Z0-9_]+", content)):
                assert name in allowed, path


def test_chat_page_requires_an_authenticated_session():
    page = _page()

    assert "supabase.auth.getUser()" in page
    assert 'redirect("/login")' in page


def test_chat_page_stays_available_to_end_users_and_is_linked_from_the_dashboard():
    page = _page()

    # Chat is intentionally not gated behind the admin role (unlike documents).
    assert '"admin"' not in page
    assert 'redirect("/dashboard")' not in page

    dashboard = DASHBOARD_PAGE.read_text(encoding="utf-8")
    assert 'href="/chat"' in dashboard


def test_phase7_adds_no_conversation_history_or_persistence():
    source = _chat()

    for forbidden in ('"/conversations"', "conversation_id", "localStorage", "sessionStorage", "indexedDB"):
        assert forbidden not in source
    # Messages only live in the current page session.
    assert "useState<ChatMessage[]>([])" in source
    assert not list(MIGRATIONS.glob("*conversation*"))
    assert not list(MIGRATIONS.glob("*chat*"))
