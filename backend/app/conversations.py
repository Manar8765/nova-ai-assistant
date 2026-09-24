"""Authenticated conversation history and persisted RAG turns."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from supabase import Client

from app.documents import AuthenticatedProfile, get_authenticated_profile, get_supabase_client
from app.rag import answer_question, validate_question

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=60)
    question: str | None = None


class MessageCreateIn(BaseModel):
    question: str


class ConversationOut(BaseModel):
    conversation_id: UUID
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    message_id: UUID
    role: str
    content: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    message_index: int
    created_at: str


class AppendMessagesOut(BaseModel):
    conversation_id: UUID
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[MessageOut]


def _database_error(message: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)


def _conversation_query(client: Client, profile: AuthenticatedProfile, conversation_id: UUID):
    return (
        client.table("conversation")
        .select("conversation_id, company_id, user_id, title, created_at, updated_at")
        .eq("conversation_id", str(conversation_id))
        .eq("company_id", profile.company_id)
        .eq("user_id", profile.user_id)
        .maybe_single()
    )


def _require_conversation(client: Client, profile: AuthenticatedProfile, conversation_id: UUID) -> dict[str, Any]:
    try:
        response = _conversation_query(client, profile, conversation_id).execute()
    except Exception as exc:
        raise _database_error("Unable to retrieve the conversation. Please try again.", exc)
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return response.data


def _public_conversation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _conversation_title(value: str | None) -> str:
    title = " ".join((value or "").split())
    return title[:60] or "New conversation"


def _public_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "message_id": row["message_id"],
            "role": row["role"],
            "content": row["content"],
            "sources": row.get("sources") or [],
            "message_index": row["message_index"],
            "created_at": row["created_at"],
        }
        for index, row in enumerate(rows)
    ]


def _recent_history(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    remaining = 4000
    count = 0
    for row in reversed(rows):
        if remaining <= 0 or count >= 6:
            break
        content = str(row["content"])
        if len(content) > remaining:
            content = content[-remaining:]
        selected.append({"role": str(row["role"]), "content": content})
        remaining -= len(content)
        count += 1
    selected.reverse()
    return selected


def _load_recent_history(
    client: Client, profile: AuthenticatedProfile, conversation_id: UUID
) -> list[dict[str, str]]:
    try:
        response = (
            client.table("message")
            .select("role, content, message_index")
            .eq("conversation_id", str(conversation_id))
            .eq("company_id", profile.company_id)
            .order("message_index", desc=True)
            .limit(6)
            .execute()
        )
    except Exception as exc:
        raise _database_error("Unable to retrieve conversation history. Please try again.", exc)
    rows = list(response.data or [])
    rows.sort(key=lambda row: row["message_index"])
    return _recent_history(rows)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
    payload: ConversationCreateIn | None = None,
) -> dict[str, Any]:
    title = _conversation_title(payload.title if payload else None)
    try:
        response = (
            client.table("conversation")
            .insert({"company_id": profile.company_id, "user_id": profile.user_id, "title": title})
            .execute()
        )
    except Exception as exc:
        raise _database_error("Unable to create the conversation. Please try again.", exc)
    if not response.data:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to create the conversation.")
    return _public_conversation(response.data[0] if isinstance(response.data, list) else response.data)


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> list[dict[str, Any]]:
    try:
        response = (
            client.table("conversation")
            .select("conversation_id, company_id, user_id, title, created_at, updated_at")
            .eq("company_id", profile.company_id)
            .eq("user_id", profile.user_id)
            .order("updated_at", desc=True)
            .execute()
        )
    except Exception as exc:
        raise _database_error("Unable to retrieve conversations. Please try again.", exc)
    return [_public_conversation(row) for row in (response.data or [])]


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: UUID,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict[str, Any]:
    return _public_conversation(_require_conversation(client, profile, conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: UUID,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> Response:
    _require_conversation(client, profile, conversation_id)
    try:
        (
            client.table("conversation")
            .delete()
            .eq("conversation_id", str(conversation_id))
            .eq("company_id", profile.company_id)
            .eq("user_id", profile.user_id)
            .execute()
        )
    except Exception as exc:
        raise _database_error("Unable to delete the conversation. Please try again.", exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: UUID,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> list[dict[str, Any]]:
    _require_conversation(client, profile, conversation_id)
    try:
        response = (
            client.table("message")
            .select("message_id, role, content, sources, message_index, created_at")
            .eq("conversation_id", str(conversation_id))
            .eq("company_id", profile.company_id)
            .order("message_index")
            .execute()
        )
    except Exception as exc:
        raise _database_error("Unable to retrieve messages. Please try again.", exc)
    return _public_messages(list(response.data or []))


@router.post("/{conversation_id}/messages", response_model=AppendMessagesOut)
def append_message(
    conversation_id: UUID,
    payload: MessageCreateIn,
    profile: Annotated[AuthenticatedProfile, Depends(get_authenticated_profile)],
    client: Annotated[Client, Depends(get_supabase_client)],
) -> dict[str, Any]:
    _require_conversation(client, profile, conversation_id)
    question = validate_question(payload.question)
    history = _load_recent_history(client, profile, conversation_id)
    result = answer_question(client, profile.company_id, question, history)
    try:
        response = client.rpc(
            "append_conversation_messages",
            {
                "p_conversation_id": str(conversation_id),
                "p_company_id": profile.company_id,
                "p_user_id": profile.user_id,
                "p_question": question,
                "p_answer": result["answer"],
                "p_sources": result["sources"],
            },
        ).execute()
    except Exception as exc:
        raise _database_error("Unable to save the conversation turn. Please try again.", exc)
    saved_messages = _public_messages(list(response.data or []))
    return {
        "conversation_id": conversation_id,
        "answer": result["answer"],
        "sources": result["sources"],
        "messages": saved_messages,
    }
