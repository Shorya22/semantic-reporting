"""
Database connection manager.

Manages live database engines that the SQL agent runs queries against.
Each connection is identified by a UUID we expose to clients as
``session_id`` (the name predates this refactor — kept for back-compat).

What's new vs the original implementation
-----------------------------------------
* Connections are persisted to the app database (``app.db.repositories``)
  so they survive backend restarts.
* When a request hits an unknown ``session_id``, we try to lazy-reconnect
  from the persisted row before returning 404 — letting the frontend
  resume seamlessly after a server restart.
* CSV / Excel uploads keep their file in ``settings.upload_dir`` and are
  re-loaded on demand if the in-memory engine has been dropped.
* Schema DDL is cached in Redis (with in-mem fallback) so we don't pay
  for a full ``get_table_info()`` on every request.

Supported source types
----------------------
* SQLite   — any local .db / .sqlite file
* Postgres — remote DB via psycopg2
* CSV      — loaded into an in-memory SQLite database via pandas
* Excel    — every sheet becomes a table in an in-memory SQLite database
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

import pandas as pd
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.cache import cache
from app.config import settings
from app.db.app_db import session_scope
from app.db.repositories import ConnectionRepo, connection_to_dict

logger = logging.getLogger(__name__)


_SCHEMA_CACHE_KEY = "schema:{cid}"


class ConnectionManager:
    """In-memory registry of *live* database engines.

    Persistence lives in the app DB; this class is the runtime cache that
    holds the actual SQLAlchemy engines and LangChain ``SQLDatabase``
    objects. A single source of truth (the app DB) plus a fast in-process
    cache gives us both durability and low-latency lookups.
    """

    def __init__(self) -> None:
        self._connections: dict[str, SQLDatabase] = {}
        self._engines: dict[str, Engine] = {}
        self._metadata: dict[str, dict] = {}

    # ==================================================================
    # Internal: build live objects from a stored Connection row
    # ==================================================================

    def _build_metadata(
        self,
        *,
        type: str,
        name: str,
        path: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        tables: Optional[list[str]] = None,
        schema_ddl: Optional[str] = None,
        extras: Optional[dict] = None,
    ) -> dict:
        meta: dict = {
            "type": type,
            "name": name,
            "tables": tables or [],
        }
        if schema_ddl is not None:
            meta["schema_ddl"] = schema_ddl
        if path is not None:
            meta["path"] = path
        if host is not None:
            meta["host"] = host
        if port is not None:
            meta["port"] = port
        if database is not None:
            meta["database"] = database
        if extras:
            meta.update(extras)
        return meta

    def _register_live(
        self,
        sid: str,
        db: SQLDatabase,
        meta: dict,
        engine: Optional[Engine] = None,
    ) -> None:
        self._connections[sid] = db
        self._metadata[sid] = meta
        if engine is not None:
            self._engines[sid] = engine

    # ==================================================================
    # Public connect helpers — persist to app DB then keep the live engine.
    # ==================================================================

    def connect_sqlite(
        self,
        db_path: str,
        session_id: Optional[str] = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        abs_path = os.path.abspath(db_path)
        uri = f"sqlite:///{abs_path}"
        db = SQLDatabase.from_uri(uri)

        tables = db.get_usable_table_names()
        schema_ddl = db.get_table_info()
        meta = self._build_metadata(
            type="sqlite", name=os.path.basename(abs_path),
            path=abs_path, tables=tables, schema_ddl=schema_ddl,
        )
        self._register_live(sid, db, meta)
        cache.set(_SCHEMA_CACHE_KEY.format(cid=sid),
                  {"tables": tables, "schema_ddl": schema_ddl},
                  ttl=settings.cache_schema_ttl)

        with session_scope() as s:
            ConnectionRepo.upsert(
                s, id=sid, type="sqlite",
                name=os.path.basename(abs_path),
                path=abs_path,
                meta={"tables": tables, "schema_ddl": schema_ddl},
            )
        return sid

    def connect_postgres(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        session_id: Optional[str] = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        uri = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        db = SQLDatabase.from_uri(uri)

        tables = db.get_usable_table_names()
        schema_ddl = db.get_table_info()
        meta = self._build_metadata(
            type="postgresql", name=database,
            host=host, port=port, database=database,
            tables=tables, schema_ddl=schema_ddl,
        )
        self._register_live(sid, db, meta)
        cache.set(_SCHEMA_CACHE_KEY.format(cid=sid),
                  {"tables": tables, "schema_ddl": schema_ddl},
                  ttl=settings.cache_schema_ttl)

        with session_scope() as s:
            ConnectionRepo.upsert(
                s, id=sid, type="postgresql",
                name=database, host=host, port=port,
                database=database, username=user,
                password_plain=password,
                meta={"tables": tables, "schema_ddl": schema_ddl},
            )
        return sid

    def load_csv(
        self,
        file_path: str,
        table_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        stem = os.path.splitext(os.path.basename(file_path))[0]
        table_name = table_name or stem.replace(" ", "_").lower()

        df: pd.DataFrame = pd.read_csv(file_path)
        engine: Engine = create_engine("sqlite://")
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        db = SQLDatabase(engine)
        schema_ddl = db.get_table_info()
        meta = self._build_metadata(
            type="csv", name=os.path.basename(file_path),
            tables=[table_name], schema_ddl=schema_ddl,
            extras={
                "file": os.path.basename(file_path),
                "table": table_name,
                "rows": int(len(df)),
                "columns": list(df.columns),
            },
        )
        self._register_live(sid, db, meta, engine=engine)
        cache.set(_SCHEMA_CACHE_KEY.format(cid=sid),
                  {"tables": [table_name], "schema_ddl": schema_ddl},
                  ttl=settings.cache_schema_ttl)

        with session_scope() as s:
            ConnectionRepo.upsert(
                s, id=sid, type="csv",
                name=os.path.basename(file_path),
                upload_path=file_path,
                meta={
                    "tables": [table_name],
                    "schema_ddl": schema_ddl,
                    "table": table_name,
                    "rows": int(len(df)),
                    "columns": list(df.columns),
                },
            )
        return sid

    def load_excel(
        self,
        file_path: str,
        session_id: Optional[str] = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        xl = pd.ExcelFile(file_path)
        engine: Engine = create_engine("sqlite://")
        tables: list[str] = []

        for sheet in xl.sheet_names:
            df: pd.DataFrame = xl.parse(sheet)
            t_name = sheet.replace(" ", "_").lower()
            df.to_sql(t_name, engine, if_exists="replace", index=False)
            tables.append(t_name)

        db = SQLDatabase(engine)
        schema_ddl = db.get_table_info()
        meta = self._build_metadata(
            type="excel", name=os.path.basename(file_path),
            tables=tables, schema_ddl=schema_ddl,
            extras={"file": os.path.basename(file_path), "sheets": list(xl.sheet_names)},
        )
        self._register_live(sid, db, meta, engine=engine)
        cache.set(_SCHEMA_CACHE_KEY.format(cid=sid),
                  {"tables": tables, "schema_ddl": schema_ddl},
                  ttl=settings.cache_schema_ttl)

        with session_scope() as s:
            ConnectionRepo.upsert(
                s, id=sid, type="excel",
                name=os.path.basename(file_path),
                upload_path=file_path,
                meta={
                    "tables": tables,
                    "schema_ddl": schema_ddl,
                    "sheets": list(xl.sheet_names),
                },
            )
        return sid

    # ==================================================================
    # Lazy rehydration — called on cache miss
    # ==================================================================

    def _rehydrate(self, session_id: str) -> bool:
        """Re-open a previously persisted connection. Returns True on success."""
        with session_scope() as s:
            row = ConnectionRepo.get(s, session_id)
            if row is None or not row.is_active:
                return False
            password = ConnectionRepo.get_password(s, session_id)
            ConnectionRepo.touch(s, session_id)
            row_type = row.type
            row_name = row.name
            row_path = row.path
            row_host = row.host
            row_port = row.port
            row_database = row.database
            row_username = row.username
            row_upload = row.upload_path
            row_meta = dict(row.meta_json or {})

        try:
            if row_type == "sqlite" and row_path and os.path.exists(row_path):
                db = SQLDatabase.from_uri(f"sqlite:///{row_path}")
                tables = db.get_usable_table_names()
                schema_ddl = db.get_table_info()
                meta = self._build_metadata(
                    type="sqlite", name=row_name, path=row_path,
                    tables=tables, schema_ddl=schema_ddl,
                )
                self._register_live(session_id, db, meta)
                return True

            if row_type == "postgresql" and row_host and row_database and row_username:
                uri = f"postgresql://{row_username}:{password or ''}@{row_host}:{row_port or 5432}/{row_database}"
                db = SQLDatabase.from_uri(uri)
                tables = db.get_usable_table_names()
                schema_ddl = db.get_table_info()
                meta = self._build_metadata(
                    type="postgresql", name=row_name,
                    host=row_host, port=row_port, database=row_database,
                    tables=tables, schema_ddl=schema_ddl,
                )
                self._register_live(session_id, db, meta)
                return True

            if row_type == "csv" and row_upload and os.path.exists(row_upload):
                self.load_csv(row_upload, session_id=session_id)
                return True

            if row_type == "excel" and row_upload and os.path.exists(row_upload):
                self.load_excel(row_upload, session_id=session_id)
                return True

        except Exception as exc:  # noqa: BLE001
            logger.warning("Rehydration failed for %s: %s", session_id, exc)
            return False

        return False

    # ==================================================================
    # Public read helpers
    # ==================================================================

    def get_db(self, session_id: str) -> Optional[SQLDatabase]:
        if session_id in self._connections:
            return self._connections[session_id]
        if self._rehydrate(session_id):
            return self._connections.get(session_id)
        return None

    def get_metadata(self, session_id: str) -> Optional[dict]:
        if session_id in self._metadata:
            return self._metadata[session_id]
        if self._rehydrate(session_id):
            return self._metadata.get(session_id)
        return None

    def get_tables(self, session_id: str) -> list[str]:
        # Cache hit path — avoid round-tripping to the engine
        cached = cache.get(_SCHEMA_CACHE_KEY.format(cid=session_id))
        if cached and "tables" in cached:
            return cached["tables"] or []
        db = self.get_db(session_id)
        if db is None:
            return []
        tables = db.get_usable_table_names()
        cache.set(
            _SCHEMA_CACHE_KEY.format(cid=session_id),
            {"tables": tables, "schema_ddl": self._metadata.get(session_id, {}).get("schema_ddl", "")},
            ttl=settings.cache_schema_ttl,
        )
        return tables

    def get_schema_ddl(self, session_id: str) -> Optional[str]:
        cached = cache.get(_SCHEMA_CACHE_KEY.format(cid=session_id))
        if cached and "schema_ddl" in cached:
            return cached["schema_ddl"]
        meta = self.get_metadata(session_id)
        return (meta or {}).get("schema_ddl")

    def is_connected(self, session_id: str) -> bool:
        if session_id in self._connections:
            return True
        return self._rehydrate(session_id)

    def list_sessions(self) -> list[dict]:
        """Return every persisted active connection (regardless of in-memory state)."""
        with session_scope() as s:
            rows = ConnectionRepo.list(s, only_active=True)
            return [connection_to_dict(r) for r in rows]

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        self._engines.pop(session_id, None)
        self._metadata.pop(session_id, None)
        cache.delete(_SCHEMA_CACHE_KEY.format(cid=session_id))
        cache.delete_prefix(f"query:{session_id}:")
        with session_scope() as s:
            ConnectionRepo.soft_delete(s, session_id)


# ---------------------------------------------------------------------------
# Module-level singleton shared across the FastAPI app.
# ---------------------------------------------------------------------------

connection_manager = ConnectionManager()
