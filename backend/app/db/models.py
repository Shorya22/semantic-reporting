"""SQLAlchemy models for the application metadata database."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.app_db import Base


class Connection(Base):
    """A persisted database connection.

    The PK ``id`` is also the runtime ``session_id`` exposed to clients —
    keeping them identical means a session_id remains valid across
    backend restarts (connection_manager re-opens the underlying engine
    on boot).

    Postgres credentials are stored encrypted in ``password_enc`` via
    ``app.security.crypto``; never read this column directly — go through
    the connection service.
    """

    __tablename__ = "connections"

    id:           Mapped[str]            = mapped_column(String(64),  primary_key=True)
    type:         Mapped[str]            = mapped_column(String(16),  nullable=False)  # sqlite|postgresql|csv|excel
    name:         Mapped[str]            = mapped_column(String(255), nullable=False)

    # SQLite
    path:         Mapped[Optional[str]]  = mapped_column(Text)

    # Postgres (password encrypted at rest)
    host:         Mapped[Optional[str]]  = mapped_column(String(255))
    port:         Mapped[Optional[int]]  = mapped_column(Integer)
    database:     Mapped[Optional[str]]  = mapped_column(String(255))
    username:     Mapped[Optional[str]]  = mapped_column(String(255))
    password_enc: Mapped[Optional[str]]  = mapped_column(Text)

    # CSV / Excel — keep the path of the uploaded file so we can re-load
    upload_path:  Mapped[Optional[str]]  = mapped_column(Text)

    # Cached metadata (table list, schema DDL) snapshot — for fast UI hydration
    meta_json:    Mapped[dict]           = mapped_column(JSON, nullable=False, default=dict)

    is_active:    Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True)

    created_at:   Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Conversation(Base):
    """A chat thread, ChatGPT-style.

    Each conversation pins a connection + model + provider at creation
    time, but those fields are mutable so the user can switch mid-thread
    (the LLM context is preserved by LangGraph's checkpointer keyed on
    ``thread_id = conversation.id``).
    """

    __tablename__ = "conversations"

    id:            Mapped[str]            = mapped_column(String(64),  primary_key=True)
    title:         Mapped[str]            = mapped_column(String(255), nullable=False, default="New chat")
    connection_id: Mapped[Optional[str]]  = mapped_column(
        String(64), ForeignKey("connections.id", ondelete="SET NULL")
    )
    model:         Mapped[Optional[str]]  = mapped_column(String(128))
    provider:      Mapped[Optional[str]]  = mapped_column(String(32))

    created_at:    Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at:    Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (
        Index("idx_conversation_updated", "updated_at"),
    )


class Message(Base):
    """A single turn in a conversation (user prompt or assistant reply).

    Assistant messages carry the rich rendering payload — chart specs,
    table data, agent reasoning steps, and token usage — as JSON blobs
    so the frontend can hydrate the analysis card on load.
    """

    __tablename__ = "messages"

    id:              Mapped[str]            = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str]            = mapped_column(
        String(64),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role:            Mapped[str]            = mapped_column(String(16), nullable=False)  # user|assistant
    content:         Mapped[str]            = mapped_column(Text, nullable=False, default="")

    charts_json:     Mapped[list]           = mapped_column(JSON, nullable=False, default=list)
    tables_json:     Mapped[list]           = mapped_column(JSON, nullable=False, default=list)
    steps_json:      Mapped[list]           = mapped_column(JSON, nullable=False, default=list)
    usage_json:      Mapped[Optional[dict]] = mapped_column(JSON)

    export_sql:      Mapped[Optional[str]]  = mapped_column(Text)
    status:          Mapped[str]            = mapped_column(String(16), nullable=False, default="done")
    error:           Mapped[Optional[str]]  = mapped_column(Text)

    created_at:      Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("idx_message_conversation", "conversation_id", "created_at"),
    )


class Preference(Base):
    """Singleton row holding the current user's UI preferences.

    Single-user app, so we store exactly one row (id=1). Multi-user
    support would split this per user_id.
    """

    __tablename__ = "preferences"

    id:                     Mapped[int]            = mapped_column(Integer, primary_key=True, default=1)
    model:                  Mapped[Optional[str]]  = mapped_column(String(128))
    provider:               Mapped[Optional[str]]  = mapped_column(String(32))
    active_connection_id:   Mapped[Optional[str]]  = mapped_column(String(64))
    active_conversation_id: Mapped[Optional[str]]  = mapped_column(String(64))
    updated_at:             Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )