"""
Repository layer for the application metadata DB.

Thin classes that own all SQL access to one model. Routes never touch
SQLAlchemy directly — they call repos. This keeps the persistence
contract obvious and makes it trivial to swap SQLite for Postgres.

Conventions:
  * All methods accept an open ``Session`` so the caller controls the
    transaction boundary (usually via ``session_scope()``).
  * ``to_dict`` helpers shape DB rows into the JSON the API returns —
    keeping wire format decisions out of the model classes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import Connection, Conversation, Message, Preference
from app.security.crypto import decrypt, encrypt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def _conn_to_dict(c: Connection) -> dict[str, Any]:
    """Public-safe shape — never includes the encrypted password."""
    base: dict[str, Any] = {
        "session_id":   c.id,  # frontend continues to call it session_id
        "id":           c.id,
        "type":         c.type,
        "name":         c.name,
        "is_active":    bool(c.is_active),
        "created_at":   c.created_at.isoformat() if c.created_at else None,
        "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
    }
    # Light source-specific projection
    if c.type == "sqlite":
        base["path"] = c.path
    if c.type == "postgresql":
        base.update({
            "host":     c.host,
            "port":     c.port,
            "database": c.database,
            "user":     c.username,
        })
    if c.type in ("csv", "excel"):
        base["file"] = c.name

    # Cached metadata (tables, schema_ddl summary, rows etc.)
    meta = c.meta_json or {}
    for key in ("tables", "rows", "columns", "sheets", "table"):
        if key in meta:
            base[key] = meta[key]
    return base


def _conv_to_dict(c: Conversation, message_count: int = 0) -> dict[str, Any]:
    return {
        "id":            c.id,
        "title":         c.title,
        "connection_id": c.connection_id,
        "model":         c.model,
        "provider":      c.provider,
        "created_at":    c.created_at.isoformat() if c.created_at else None,
        "updated_at":    c.updated_at.isoformat() if c.updated_at else None,
        "message_count": message_count,
    }


def _msg_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id":              m.id,
        "conversation_id": m.conversation_id,
        "role":            m.role,
        "content":         m.content,
        "charts":          m.charts_json or [],
        "tables":          m.tables_json or [],
        "steps":           m.steps_json or [],
        "usage":           m.usage_json,
        "export_sql":      m.export_sql,
        "status":          m.status,
        "error":           m.error,
        "created_at":      m.created_at.isoformat() if m.created_at else None,
    }


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

class ConnectionRepo:
    @staticmethod
    def upsert(
        session: Session,
        *,
        id: Optional[str],
        type: str,
        name: str,
        path: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password_plain: Optional[str] = None,
        upload_path: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Connection:
        """Create or update a connection row.

        ``password_plain`` is encrypted before storage. Pass ``None`` to
        leave the existing password untouched on update.
        """
        cid = id or _new_id()
        existing: Optional[Connection] = session.get(Connection, cid)

        if existing is None:
            existing = Connection(
                id=cid,
                type=type,
                name=name,
                path=path,
                host=host,
                port=port,
                database=database,
                username=username,
                password_enc=encrypt(password_plain) if password_plain else None,
                upload_path=upload_path,
                meta_json=meta or {},
                is_active=True,
                last_used_at=datetime.utcnow(),
            )
            session.add(existing)
            return existing

        # Update mutable fields
        existing.type = type
        existing.name = name
        existing.path = path
        existing.host = host
        existing.port = port
        existing.database = database
        existing.username = username
        if password_plain is not None:
            existing.password_enc = encrypt(password_plain) if password_plain else None
        existing.upload_path = upload_path
        existing.meta_json = meta or existing.meta_json or {}
        existing.is_active = True
        existing.last_used_at = datetime.utcnow()
        return existing

    @staticmethod
    def get(session: Session, cid: str) -> Optional[Connection]:
        return session.get(Connection, cid)

    @staticmethod
    def get_password(session: Session, cid: str) -> Optional[str]:
        c = session.get(Connection, cid)
        if c is None or not c.password_enc:
            return None
        return decrypt(c.password_enc)

    @staticmethod
    def list(session: Session, *, only_active: bool = True) -> list[Connection]:
        stmt = select(Connection).order_by(desc(Connection.last_used_at))
        if only_active:
            stmt = stmt.where(Connection.is_active.is_(True))
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def soft_delete(session: Session, cid: str) -> bool:
        c = session.get(Connection, cid)
        if c is None:
            return False
        c.is_active = False
        return True

    @staticmethod
    def hard_delete(session: Session, cid: str) -> bool:
        c = session.get(Connection, cid)
        if c is None:
            return False
        session.delete(c)
        return True

    @staticmethod
    def touch(session: Session, cid: str) -> None:
        c = session.get(Connection, cid)
        if c is not None:
            c.last_used_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

class ConversationRepo:
    @staticmethod
    def create(
        session: Session,
        *,
        title: str = "New chat",
        connection_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Conversation:
        c = Conversation(
            id=_new_id(),
            title=title,
            connection_id=connection_id,
            model=model,
            provider=provider,
        )
        session.add(c)
        session.flush()
        return c

    @staticmethod
    def get(session: Session, cid: str) -> Optional[Conversation]:
        return session.get(Conversation, cid)

    @staticmethod
    def list(session: Session, *, limit: int = 200) -> list[tuple[Conversation, int]]:
        """Return [(conversation, message_count)] sorted by recent activity."""
        from sqlalchemy import func as sa_func

        stmt = (
            select(Conversation, sa_func.count(Message.id))
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .group_by(Conversation.id)
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
        )
        return [(c, int(n)) for c, n in session.execute(stmt).all()]

    @staticmethod
    def update(
        session: Session,
        cid: str,
        *,
        title: Optional[str] = None,
        connection_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[Conversation]:
        c = session.get(Conversation, cid)
        if c is None:
            return None
        if title is not None:
            c.title = title
        if connection_id is not None:
            c.connection_id = connection_id
        if model is not None:
            c.model = model
        if provider is not None:
            c.provider = provider
        c.updated_at = datetime.utcnow()
        return c

    @staticmethod
    def touch(session: Session, cid: str) -> None:
        c = session.get(Conversation, cid)
        if c is not None:
            c.updated_at = datetime.utcnow()

    @staticmethod
    def delete(session: Session, cid: str) -> bool:
        c = session.get(Conversation, cid)
        if c is None:
            return False
        session.delete(c)
        return True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class MessageRepo:
    @staticmethod
    def add(
        session: Session,
        *,
        conversation_id: str,
        role: str,
        content: str = "",
        charts: Optional[list] = None,
        tables: Optional[list] = None,
        steps: Optional[list] = None,
        usage: Optional[dict] = None,
        export_sql: Optional[str] = None,
        status: str = "done",
        error: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Message:
        m = Message(
            id=message_id or _new_id(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            charts_json=charts or [],
            tables_json=tables or [],
            steps_json=steps or [],
            usage_json=usage,
            export_sql=export_sql,
            status=status,
            error=error,
        )
        session.add(m)
        session.flush()
        return m

    @staticmethod
    def list(session: Session, conversation_id: str) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class PreferenceRepo:
    @staticmethod
    def get(session: Session) -> Preference:
        p = session.get(Preference, 1)
        if p is None:
            p = Preference(id=1)
            session.add(p)
            session.flush()
        return p

    @staticmethod
    def update(
        session: Session,
        *,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        active_connection_id: Optional[str] = None,
        active_conversation_id: Optional[str] = None,
    ) -> Preference:
        p = PreferenceRepo.get(session)
        if model is not None:
            p.model = model
        if provider is not None:
            p.provider = provider
        if active_connection_id is not None:
            p.active_connection_id = active_connection_id or None
        if active_conversation_id is not None:
            p.active_conversation_id = active_conversation_id or None
        p.updated_at = datetime.utcnow()
        return p

    @staticmethod
    def to_dict(p: Preference) -> dict[str, Any]:
        return {
            "model":                  p.model,
            "provider":               p.provider,
            "active_connection_id":   p.active_connection_id,
            "active_conversation_id": p.active_conversation_id,
            "updated_at":             p.updated_at.isoformat() if p.updated_at else None,
        }


# Public wire-format helpers (re-exported for convenience)
connection_to_dict = _conn_to_dict
conversation_to_dict = _conv_to_dict
message_to_dict = _msg_to_dict
