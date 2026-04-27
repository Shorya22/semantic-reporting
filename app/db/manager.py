"""
Database connection manager.

Manages multiple live database sessions in memory. Each session wraps a
LangChain `SQLDatabase` object and exposes helpers used by the agent layer
and the REST API.

Supported source types
----------------------
- SQLite   – any local .db / .sqlite file
- PostgreSQL – remote database via psycopg2
- CSV       – loaded into an in-memory SQLite database via pandas
- Excel     – every sheet becomes a table in an in-memory SQLite database
"""

import os
import uuid
from typing import Optional

import pandas as pd
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class ConnectionManager:
    """
    In-memory registry of active database sessions.

    Each session is identified by a UUID string and stores:
      - A ``SQLDatabase`` instance (used by LangChain tools)
      - An optional SQLAlchemy ``Engine`` (kept alive for in-memory DBs)
      - A metadata dict describing the connection (type, name, tables, etc.)

    Thread-safety note: this implementation is intentionally simple (dict-based).
    For production use with multiple workers, replace with a shared store such as
    Redis or a process-safe cache.
    """

    def __init__(self) -> None:
        self._connections: dict[str, SQLDatabase] = {}
        self._engines: dict[str, Engine] = {}
        self._metadata: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public connect helpers
    # ------------------------------------------------------------------

    def connect_sqlite(
        self,
        db_path: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Open a connection to a local SQLite file and register it as a session.

        Parameters
        ----------
        db_path:
            Absolute or relative path to the ``.db`` / ``.sqlite`` file.
            The path is resolved to an absolute form before storing.
        session_id:
            Optional caller-supplied UUID. A new one is generated when omitted.

        Returns
        -------
        str
            The session ID that identifies this connection.

        Raises
        ------
        sqlalchemy.exc.OperationalError
            If SQLAlchemy cannot open the file (e.g. corrupted or missing).
        """
        session_id = session_id or str(uuid.uuid4())
        abs_path = os.path.abspath(db_path)
        uri = f"sqlite:///{abs_path}"
        db = SQLDatabase.from_uri(uri)
        self._connections[session_id] = db
        self._metadata[session_id] = {
            "type": "sqlite",
            "path": abs_path,
            "name": os.path.basename(abs_path),
            "tables": db.get_usable_table_names(),
            "schema_ddl": db.get_table_info(),
        }
        return session_id

    def connect_postgres(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Open a connection to a PostgreSQL database and register it as a session.

        Parameters
        ----------
        host:
            Hostname or IP address of the PostgreSQL server.
        port:
            TCP port (default is 5432).
        database:
            Name of the database to connect to.
        user:
            PostgreSQL username.
        password:
            PostgreSQL password.
        session_id:
            Optional caller-supplied UUID. A new one is generated when omitted.

        Returns
        -------
        str
            The session ID that identifies this connection.

        Raises
        ------
        sqlalchemy.exc.OperationalError
            If the connection attempt fails (wrong credentials, network, etc.).
        """
        session_id = session_id or str(uuid.uuid4())
        uri = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        db = SQLDatabase.from_uri(uri)
        self._connections[session_id] = db
        self._metadata[session_id] = {
            "type": "postgresql",
            "host": host,
            "port": port,
            "database": database,
            "name": database,
            "tables": db.get_usable_table_names(),
            "schema_ddl": db.get_table_info(),
        }
        return session_id

    def load_csv(
        self,
        file_path: str,
        table_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Load a CSV file into an in-memory SQLite database and register the session.

        The CSV is parsed with ``pandas.read_csv`` and written to a single table
        inside an in-memory SQLite engine (``sqlite://``). The engine is kept alive
        for the duration of the session so the data is not lost.

        Parameters
        ----------
        file_path:
            Absolute path to the ``.csv`` file.
        table_name:
            Name to give the table. Defaults to the file stem with spaces
            replaced by underscores and lowercased.
        session_id:
            Optional caller-supplied UUID. A new one is generated when omitted.

        Returns
        -------
        str
            The session ID that identifies this connection.

        Raises
        ------
        pandas.errors.ParserError
            If the file is not a valid CSV.
        """
        session_id = session_id or str(uuid.uuid4())
        stem = os.path.splitext(os.path.basename(file_path))[0]
        table_name = table_name or stem.replace(" ", "_").lower()

        df: pd.DataFrame = pd.read_csv(file_path)
        engine: Engine = create_engine("sqlite://")
        df.to_sql(table_name, engine, if_exists="replace", index=False)

        db = SQLDatabase(engine)
        self._connections[session_id] = db
        self._engines[session_id] = engine
        self._metadata[session_id] = {
            "type": "csv",
            "file": os.path.basename(file_path),
            "name": os.path.basename(file_path),
            "table": table_name,
            "rows": int(len(df)),
            "columns": list(df.columns),
            "tables": [table_name],
            "schema_ddl": db.get_table_info(),
        }
        return session_id

    def load_excel(
        self,
        file_path: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Load an Excel workbook into an in-memory SQLite database and register the session.

        Every sheet in the workbook becomes a separate table. Sheet names are
        lowercased and spaces are replaced with underscores. The in-memory
        SQLite engine is retained for the lifetime of the session.

        Parameters
        ----------
        file_path:
            Absolute path to the ``.xlsx`` or ``.xls`` file.
        session_id:
            Optional caller-supplied UUID. A new one is generated when omitted.

        Returns
        -------
        str
            The session ID that identifies this connection.

        Raises
        ------
        ValueError
            If the file has no readable sheets.
        """
        session_id = session_id or str(uuid.uuid4())
        xl = pd.ExcelFile(file_path)
        engine: Engine = create_engine("sqlite://")
        tables: list[str] = []

        for sheet in xl.sheet_names:
            df: pd.DataFrame = xl.parse(sheet)
            t_name = sheet.replace(" ", "_").lower()
            df.to_sql(t_name, engine, if_exists="replace", index=False)
            tables.append(t_name)

        db = SQLDatabase(engine)
        self._connections[session_id] = db
        self._engines[session_id] = engine
        self._metadata[session_id] = {
            "type": "excel",
            "file": os.path.basename(file_path),
            "name": os.path.basename(file_path),
            "sheets": list(xl.sheet_names),
            "tables": tables,
            "schema_ddl": db.get_table_info(),
        }
        return session_id

    # ------------------------------------------------------------------
    # Public read helpers
    # ------------------------------------------------------------------

    def get_db(self, session_id: str) -> Optional[SQLDatabase]:
        """
        Return the ``SQLDatabase`` for the given session, or ``None`` if not found.

        Parameters
        ----------
        session_id:
            UUID string returned by one of the connect/load methods.
        """
        return self._connections.get(session_id)

    def get_metadata(self, session_id: str) -> Optional[dict]:
        """
        Return the metadata dict for the given session, or ``None`` if not found.

        The metadata dict always contains at minimum:
        ``type``, ``name``, and ``tables`` keys.
        """
        return self._metadata.get(session_id)

    def get_tables(self, session_id: str) -> list[str]:
        """
        Return the list of usable table names for the given session.

        Returns an empty list if the session does not exist.
        """
        db = self.get_db(session_id)
        return db.get_usable_table_names() if db else []

    def is_connected(self, session_id: str) -> bool:
        """Return ``True`` if a live connection exists for the given session ID."""
        return session_id in self._connections

    def list_sessions(self) -> list[dict]:
        """
        Return a list of all active session metadata dicts.

        Each entry includes the ``session_id`` key alongside the stored metadata.
        """
        return [
            {"session_id": sid, **meta} for sid, meta in self._metadata.items()
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def disconnect(self, session_id: str) -> None:
        """
        Remove a session and release its resources.

        For in-memory databases (CSV / Excel), dropping the engine reference
        frees the memory. For file-based or remote databases the underlying
        SQLAlchemy connection pool handles cleanup.

        Parameters
        ----------
        session_id:
            UUID string of the session to remove.
        """
        self._connections.pop(session_id, None)
        self._engines.pop(session_id, None)
        self._metadata.pop(session_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton shared across the FastAPI app
# ---------------------------------------------------------------------------

connection_manager = ConnectionManager()
"""
Global ``ConnectionManager`` instance injected into routes and agent helpers.

Importing this object from other modules gives access to the same in-memory
session registry for the lifetime of the process.
"""
