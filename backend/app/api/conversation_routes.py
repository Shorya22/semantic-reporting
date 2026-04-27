"""
Conversation, message, and preference routes.

Mounted under ``/api/v1`` via the router prefix below. Kept separate from the
NL/DB routes so the diff stays surgical and reviewable.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    ApiResponse,
    ConversationCreate,
    ConversationUpdate,
    PreferenceUpdate,
)
from app.db.app_db import session_scope
from app.db.repositories import (
    ConversationRepo,
    MessageRepo,
    PreferenceRepo,
    conversation_to_dict,
    message_to_dict,
)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Reusable response examples
# ---------------------------------------------------------------------------

ERR_404_CONV = {
    "description": "Conversation not found.",
    "content": {
        "application/json": {
            "example": {"detail": "Conversation 'c-1234' not found."}
        }
    },
}

CONV_EXAMPLE = {
    "id":             "c-9f3c1b2a",
    "title":          "Top expense categories",
    "connection_id":  "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
    "model":          "llama-3.3-70b-versatile",
    "provider":       "groq",
    "created_at":     "2026-04-27T11:30:00Z",
    "updated_at":     "2026-04-27T11:31:42Z",
    "message_count":  4,
}

MESSAGE_EXAMPLE = {
    "id":              "m-1a2b3c4d",
    "conversation_id": "c-9f3c1b2a",
    "role":            "assistant",
    "content":         "The top 5 categories are Groceries ($12,400.50), …",
    "charts":          [{"id": "ch-1", "title": "Top categories", "option": {}, "sql": "SELECT …"}],
    "tables":          [{"id": "t-1",  "title": "Top categories", "columns": ["category", "total"], "rows": [["Groceries", 12400.50]], "sql": "SELECT …"}],
    "steps":           [{"type": "tool_start", "tool": "execute_sql"}],
    "usage":           {"input_tokens": 2341, "output_tokens": 187, "total_tokens": 2528, "latency_ms": 4823},
    "export_sql":      "SELECT category, SUM(amount) AS total FROM transactions GROUP BY category",
    "status":          "done",
    "error":           None,
    "created_at":      "2026-04-27T11:31:42Z",
}

PREF_EXAMPLE = {
    "model":                  "llama-3.3-70b-versatile",
    "provider":               "groq",
    "active_connection_id":   "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
    "active_conversation_id": "c-9f3c1b2a",
}


# =============================================================================
# Conversations
# =============================================================================


@router.get(
    "/conversations",
    tags=["Conversations"],
    response_model=ApiResponse,
    summary="List conversations",
    description=(
        "Returns all conversations sorted by most-recent activity (newest first). "
        "Each entry includes the message count."
    ),
    responses={
        200: {
            "description": "Conversation list (may be empty).",
            "content": {
                "application/json": {
                    "example": {"data": [CONV_EXAMPLE], "error": None}
                }
            },
        }
    },
)
def list_conversations() -> dict:
    with session_scope() as s:
        rows = ConversationRepo.list(s)
        return {
            "data": [conversation_to_dict(c, n) for c, n in rows],
            "error": None,
        }


@router.post(
    "/conversations",
    tags=["Conversations"],
    response_model=ApiResponse,
    status_code=201,
    summary="Create a new conversation",
    description=(
        "Creates an empty conversation thread. All four body fields are optional — "
        "`title` defaults to *New chat*, the others default to `null`."
    ),
    responses={
        201: {
            "description": "Conversation created.",
            "content": {
                "application/json": {
                    "example": {"data": {**CONV_EXAMPLE, "message_count": 0}, "error": None}
                }
            },
        }
    },
)
def create_conversation(req: ConversationCreate) -> dict:
    with session_scope() as s:
        conv = ConversationRepo.create(
            s,
            title=req.title or "New chat",
            connection_id=req.connection_id,
            model=req.model,
            provider=req.provider,
        )
        return {"data": conversation_to_dict(conv), "error": None}


@router.get(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    response_model=ApiResponse,
    summary="Get a conversation with all its messages",
    description="Returns the conversation metadata plus the full ordered message list.",
    responses={
        200: {
            "description": "Conversation found.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "conversation": CONV_EXAMPLE,
                            "messages":     [MESSAGE_EXAMPLE],
                        },
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_CONV,
    },
)
def get_conversation(conversation_id: str) -> dict:
    with session_scope() as s:
        conv = ConversationRepo.get(s, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
        msgs = MessageRepo.list(s, conversation_id)
        return {
            "data": {
                "conversation": conversation_to_dict(conv, len(msgs)),
                "messages":     [message_to_dict(m) for m in msgs],
            },
            "error": None,
        }


@router.patch(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    response_model=ApiResponse,
    summary="Rename or rebind a conversation",
    description=(
        "Partial update — only the supplied fields are changed. Use this to rename a "
        "conversation, switch its bound DB session, or change the model/provider."
    ),
    responses={
        200: {
            "description": "Conversation updated.",
            "content": {
                "application/json": {
                    "example": {"data": {**CONV_EXAMPLE, "title": "Renamed thread"}, "error": None}
                }
            },
        },
        404: ERR_404_CONV,
    },
)
def update_conversation(conversation_id: str, req: ConversationUpdate) -> dict:
    with session_scope() as s:
        conv = ConversationRepo.update(
            s, conversation_id,
            title=req.title,
            connection_id=req.connection_id,
            model=req.model,
            provider=req.provider,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
        return {"data": conversation_to_dict(conv), "error": None}


@router.delete(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    response_model=ApiResponse,
    summary="Delete a conversation and all its messages",
    description="Cascade-deletes the conversation and every message attached to it. Irreversible.",
    responses={
        200: {
            "description": "Conversation deleted.",
            "content": {
                "application/json": {
                    "example": {"data": {"deleted": "c-9f3c1b2a"}, "error": None}
                }
            },
        },
        404: ERR_404_CONV,
    },
)
def delete_conversation(conversation_id: str) -> dict:
    with session_scope() as s:
        ok = ConversationRepo.delete(s, conversation_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
        return {"data": {"deleted": conversation_id}, "error": None}


@router.get(
    "/conversations/{conversation_id}/messages",
    tags=["Conversations"],
    response_model=ApiResponse,
    summary="List messages in a conversation",
    description="Returns the ordered message list (user prompts + assistant replies).",
    responses={
        200: {
            "description": "Messages returned.",
            "content": {
                "application/json": {
                    "example": {"data": [MESSAGE_EXAMPLE], "error": None}
                }
            },
        },
        404: ERR_404_CONV,
    },
)
def list_messages(conversation_id: str) -> dict:
    with session_scope() as s:
        conv = ConversationRepo.get(s, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
        msgs = MessageRepo.list(s, conversation_id)
        return {"data": [message_to_dict(m) for m in msgs], "error": None}


# =============================================================================
# Preferences
# =============================================================================


@router.get(
    "/preferences",
    tags=["Preferences"],
    response_model=ApiResponse,
    summary="Get user preferences",
    description=(
        "Returns the singleton preferences row — model, provider, active connection, "
        "and active conversation. Used by the UI to rehydrate state on browser refresh."
    ),
    responses={
        200: {
            "description": "Preferences returned (may be all-null on a fresh install).",
            "content": {
                "application/json": {
                    "example": {"data": PREF_EXAMPLE, "error": None}
                }
            },
        }
    },
)
def get_preferences() -> dict:
    with session_scope() as s:
        return {"data": PreferenceRepo.to_dict(PreferenceRepo.get(s)), "error": None}


@router.patch(
    "/preferences",
    tags=["Preferences"],
    response_model=ApiResponse,
    summary="Update user preferences",
    description="Partial update — only the supplied fields are changed.",
    responses={
        200: {
            "description": "Preferences updated.",
            "content": {
                "application/json": {
                    "example": {"data": PREF_EXAMPLE, "error": None}
                }
            },
        }
    },
)
def update_preferences(req: PreferenceUpdate) -> dict:
    with session_scope() as s:
        p = PreferenceRepo.update(
            s,
            model=req.model,
            provider=req.provider,
            active_connection_id=req.active_connection_id,
            active_conversation_id=req.active_conversation_id,
        )
        return {"data": PreferenceRepo.to_dict(p), "error": None}
