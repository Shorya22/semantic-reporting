"""
Conversation persistence helpers.

Centralises all writes to the conversation/message tables so the route
layer can stay focused on HTTP concerns. Every helper opens its own
``session_scope`` — callers don't need to manage transactions.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.db.app_db import session_scope
from app.db.repositories import (
    ConversationRepo,
    MessageRepo,
    conversation_to_dict,
    message_to_dict,
)

logger = logging.getLogger(__name__)


# Keep titles short and clean for the sidebar.
_TITLE_MAX = 60


def _derive_title(question: str) -> str:
    text = (question or "").strip().splitlines()[0] if question else ""
    if not text:
        return "New chat"
    if len(text) <= _TITLE_MAX:
        return text
    return text[: _TITLE_MAX - 1].rstrip() + "…"


def get_or_create_conversation(
    *,
    conversation_id: Optional[str],
    question: str,
    connection_id: Optional[str],
    model: Optional[str],
    provider: Optional[str],
) -> Optional[dict[str, Any]]:
    """Resolve a conversation to attach this query to.

    * If ``conversation_id`` references an existing conversation → return it.
    * If it's missing or unknown → create a new one titled from the question.
    * Returns ``None`` if persistence is disabled (caller passed an empty
      conversation_id explicitly via no-op flow). Today we always return a
      conversation when the caller invokes this helper.
    """
    with session_scope() as s:
        if conversation_id:
            existing = ConversationRepo.get(s, conversation_id)
            if existing is not None:
                # Update model/provider if the user switched — last write wins.
                ConversationRepo.update(
                    s, conversation_id,
                    connection_id=connection_id,
                    model=model,
                    provider=provider,
                )
                return conversation_to_dict(existing)

        created = ConversationRepo.create(
            s,
            title=_derive_title(question),
            connection_id=connection_id,
            model=model,
            provider=provider,
        )
        return conversation_to_dict(created)


def append_user_message(
    *,
    conversation_id: str,
    question: str,
    message_id: Optional[str] = None,
) -> dict[str, Any]:
    with session_scope() as s:
        msg = MessageRepo.add(
            s,
            conversation_id=conversation_id,
            role="user",
            content=question,
            message_id=message_id,
        )
        ConversationRepo.touch(s, conversation_id)
        return message_to_dict(msg)


def append_assistant_message(
    *,
    conversation_id: str,
    answer: str,
    charts: list,
    tables: list,
    steps: list,
    usage: Optional[dict],
    export_sql: Optional[str],
    status: str = "done",
    error: Optional[str] = None,
    message_id: Optional[str] = None,
    visuals: Optional[list] = None,
    insight_report: Optional[dict] = None,
    critique: Optional[dict] = None,
) -> dict[str, Any]:
    with session_scope() as s:
        msg = MessageRepo.add(
            s,
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            charts=charts,
            tables=tables,
            steps=steps,
            usage=usage,
            export_sql=export_sql,
            status=status,
            error=error,
            message_id=message_id,
            visuals=visuals,
            insight_report=insight_report,
            critique=critique,
        )
        ConversationRepo.touch(s, conversation_id)
        return message_to_dict(msg)


def new_message_id() -> str:
    return str(uuid.uuid4())
