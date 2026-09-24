from pathlib import Path
from uuid import UUID

from app import conversations
from app.conversations import _conversation_title, _recent_history
from app.documents import AuthenticatedProfile
from app.generation import SYSTEM_INSTRUCTION, build_prompt


MIGRATION = (
    Path(__file__).parents[2]
    / "supabase"
    / "migrations"
    / "20260925010000_create_conversations_and_messages.sql"
)


def test_conversation_title_is_trimmed_and_capped():
    assert _conversation_title("  A   useful question  ") == "A useful question"
    assert len(_conversation_title("x" * 100)) == 60
    assert _conversation_title("") == "New conversation"


def test_recent_history_prefers_latest_six_and_preserves_order():
    rows = [{"role": "user", "content": str(index), "message_index": index} for index in range(8)]

    assert _recent_history(rows) == [
        {"role": "user", "content": str(index)} for index in range(2, 8)
    ]


def test_recent_history_stays_within_approximately_4000_characters():
    rows = [
        {"role": "user", "content": "old" * 1000, "message_index": 0},
        {"role": "assistant", "content": "new" * 1000, "message_index": 1},
    ]

    history = _recent_history(rows)

    assert len(history) <= 2
    assert sum(len(item["content"]) for item in history) <= 4000
    assert history[-1]["content"] == "new" * 1000


def test_generation_marks_history_as_non_factual_and_keeps_kb_context_authoritative():
    prompt = build_prompt(
        "How long?",
        "[Source 1]\nThe policy says 30 days.",
        [{"role": "user", "content": "We discussed another policy."}],
    )

    assert "Recent conversation context (not factual evidence)" in prompt
    assert "The policy says 30 days." in prompt
    assert "We discussed another policy." in prompt
    assert "All factual claims must come from the supplied company knowledge context." in SYSTEM_INSTRUCTION


def test_conversation_migration_has_tenant_security_and_atomic_indexes():
    sql = MIGRATION.read_text(encoding="utf-8")

    for expected in (
        "unique (conversation_id, company_id)",
        "message_index integer not null",
        "unique (conversation_id, message_index)",
        "foreign key (conversation_id, company_id)",
        "conversation_user_updated_idx",
        "conversation_company_user_updated_idx",
        "enable row level security",
        "append_conversation_messages",
        "pg_advisory_xact_lock",
        "grant execute on function",
    ):
        assert expected in sql


class _DeleteQuery:
    def __init__(self, client):
        self.client = client

    def delete(self):
        self.client.delete_called = True
        return self

    def eq(self, key, value):
        self.client.filters[key] = value
        return self

    def execute(self):
        self.client.deleted = True
        return type("Response", (), {"data": []})()


class _DeleteClient:
    def __init__(self):
        self.filters = {}
        self.delete_called = False
        self.deleted = False

    def table(self, name):
        assert name == "conversation"
        return _DeleteQuery(self)


def test_delete_conversation_deletes_only_the_authenticated_users_company_row(monkeypatch):
    client = _DeleteClient()
    profile = AuthenticatedProfile(
        user_id="33333333-3333-3333-3333-333333333333",
        company_id="11111111-1111-1111-1111-111111111111",
        role="user",
    )

    monkeypatch.setattr(conversations, "_require_conversation", lambda *_args: {})
    response = conversations.delete_conversation(
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        profile,
        client,
    )

    assert response.status_code == 204
    assert client.delete_called is True
    assert client.deleted is True
    assert client.filters == {
        "conversation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "company_id": profile.company_id,
        "user_id": profile.user_id,
    }
