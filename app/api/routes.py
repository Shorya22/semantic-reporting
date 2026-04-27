"""
FastAPI route definitions for the NL-DB Query API.

All routes are mounted under the ``/api/v1`` prefix (configured in
``app/main.py``).  The route handlers follow a thin-controller pattern:
validate → delegate → respond.  Business logic lives in
``app.db.manager`` (connection management) and ``app.agents.sql_agent``
(LangGraph agent execution).

Endpoint summary
----------------
GET    /config                       – return server configuration (default model, etc.)

POST   /connections/sqlite          – open a SQLite connection
POST   /connections/postgres        – open a PostgreSQL connection
POST   /connections/upload          – upload a CSV or Excel file and create a session
GET    /connections                  – list all active sessions
GET    /connections/{session_id}     – get metadata for one session
DELETE /connections/{session_id}     – close and remove a session
GET    /connections/{session_id}/tables – list usable table names

POST   /query                        – run a NL query (non-streaming, full result)
POST   /query/stream                 – run a NL query and stream the response via SSE
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import Response, StreamingResponse
import aiofiles

from app.agents.sql_agent import evict_session_agents, run_query, stream_query
from app.api.schemas import (
    ApiResponse,
    ChartRequest,
    ExportRequest,
    PostgresConnectRequest,
    QueryRequest,
    QueryStep,
    SQLiteConnectRequest,
)
from app.config import settings
from app.db.manager import connection_manager
from app.security.sql_guard import validate_read_only

router = APIRouter()


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/config",
    response_model=ApiResponse,
    summary="Server configuration",
    description="Returns read-only server configuration visible to the frontend.",
)
def get_config() -> dict:
    """Return public server configuration."""
    return {
        "data": {
            "default_model": settings.default_model,
            "llm_provider": settings.llm_provider,
            "ollama_base_url": settings.ollama_base_url,
        },
        "error": None,
    }


@router.get(
    "/ollama/models",
    response_model=ApiResponse,
    summary="List available Ollama models",
    description="Queries the local Ollama server and returns the list of downloaded models.",
)
async def get_ollama_models() -> dict:
    """Fetch the model list from the Ollama server at OLLAMA_BASE_URL."""
    def _fetch() -> list:
        url = f"{settings.ollama_base_url}/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read())
        return [{"id": m["name"], "label": m["name"]} for m in data.get("models", [])]

    try:
        models = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        return {"data": models, "error": None}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama not reachable at {settings.ollama_base_url}: {str(exc)[:200]}",
        ) from exc


# ---------------------------------------------------------------------------
# Connection endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/connections/sqlite",
    response_model=ApiResponse,
    summary="Connect to a SQLite database",
    description=(
        "Opens a connection to a local SQLite file and registers a session.  "
        "Returns a ``session_id`` UUID that must be supplied in subsequent query requests."
    ),
)
def connect_sqlite(req: SQLiteConnectRequest) -> dict:
    """
    Connect to a SQLite database file.

    Parameters
    ----------
    req:
        ``SQLiteConnectRequest`` containing the file path and an optional
        caller-supplied ``session_id``.

    Returns
    -------
    dict
        ApiResponse envelope where ``data`` contains:
        ``session_id``, ``type``, ``name``, ``path``, ``tables``.

    Raises
    ------
    HTTPException 404
        If the file does not exist at the given path.
    HTTPException 400
        If SQLAlchemy cannot open the file (e.g. wrong format or permissions).
    """
    if not os.path.exists(req.db_path):
        raise HTTPException(
            status_code=404,
            detail=f"SQLite file not found: {req.db_path}",
        )
    try:
        session_id = connection_manager.connect_sqlite(req.db_path, req.session_id)
        meta = connection_manager.get_metadata(session_id)
        return {"data": {"session_id": session_id, **meta}, "error": None}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/connections/postgres",
    response_model=ApiResponse,
    summary="Connect to a PostgreSQL database",
    description=(
        "Opens a connection to a remote PostgreSQL database.  "
        "Returns a ``session_id`` UUID for subsequent query requests."
    ),
)
def connect_postgres(req: PostgresConnectRequest) -> dict:
    """
    Connect to a PostgreSQL database.

    Parameters
    ----------
    req:
        ``PostgresConnectRequest`` with host, port, database, user, and password.

    Returns
    -------
    dict
        ApiResponse envelope where ``data`` contains:
        ``session_id``, ``type``, ``name``, ``host``, ``port``, ``database``, ``tables``.

    Raises
    ------
    HTTPException 400
        If the connection attempt fails (wrong credentials, unreachable host, etc.).
    """
    try:
        session_id = connection_manager.connect_postgres(
            req.host, req.port, req.database, req.user, req.password, req.session_id
        )
        meta = connection_manager.get_metadata(session_id)
        return {"data": {"session_id": session_id, **meta}, "error": None}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/connections/upload",
    response_model=ApiResponse,
    summary="Upload a CSV or Excel file",
    description=(
        "Accepts a ``multipart/form-data`` upload of a ``.csv``, ``.xlsx``, or ``.xls`` "
        "file.  The file is loaded into an in-memory SQLite database and a session is "
        "created.  Returns ``session_id``, table names, and basic schema information."
    ),
)
async def upload_file(file: UploadFile = File(..., description="CSV or Excel file to upload")) -> dict:
    """
    Upload a CSV or Excel file and create an in-memory database session.

    The uploaded file is saved to ``settings.upload_dir``, parsed with pandas,
    and inserted into an in-memory SQLite database so the SQL agent can query it.

    Parameters
    ----------
    file:
        Uploaded file.  Must have a ``.csv``, ``.xlsx``, or ``.xls`` extension.

    Returns
    -------
    dict
        ApiResponse envelope where ``data`` contains:
        ``session_id``, ``type``, ``name``, ``tables``, and for CSV also
        ``rows`` and ``columns``.

    Raises
    ------
    HTTPException 400
        If the file extension is not supported or parsing fails.
    """
    filename: str = file.filename or "upload"
    ext: str = os.path.splitext(filename)[1].lower()

    if ext not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'.  Only .csv, .xlsx, and .xls are accepted.",
        )

    save_path = os.path.join(settings.upload_dir, f"{uuid.uuid4()}_{filename}")
    try:
        async with aiofiles.open(save_path, "wb") as fp:
            await fp.write(await file.read())

        if ext == ".csv":
            session_id = connection_manager.load_csv(save_path)
        else:
            session_id = connection_manager.load_excel(save_path)

        meta = connection_manager.get_metadata(session_id)
        return {"data": {"session_id": session_id, **meta}, "error": None}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/connections",
    response_model=ApiResponse,
    summary="List all active database sessions",
    description="Returns metadata for every currently active database connection.",
)
def list_connections() -> dict:
    """Return metadata for all active sessions."""
    return {"data": connection_manager.list_sessions(), "error": None}


@router.get(
    "/connections/{session_id}",
    response_model=ApiResponse,
    summary="Get a single session's metadata",
    description="Returns connection metadata (type, name, tables, etc.) for one session.",
)
def get_connection(session_id: str) -> dict:
    """
    Retrieve metadata for a specific active session.

    Parameters
    ----------
    session_id:
        UUID string returned when the session was created.

    Raises
    ------
    HTTPException 404
        If no active session with the given ID exists.
    """
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    meta = connection_manager.get_metadata(session_id)
    return {"data": {"session_id": session_id, **meta}, "error": None}


@router.delete(
    "/connections/{session_id}",
    response_model=ApiResponse,
    summary="Close and remove a database session",
    description=(
        "Disconnects and removes the session from memory.  "
        "For CSV/Excel sessions this also releases the in-memory SQLite database."
    ),
)
def disconnect(session_id: str) -> dict:
    """
    Close an active session.

    Parameters
    ----------
    session_id:
        UUID string of the session to close.

    Raises
    ------
    HTTPException 404
        If no active session with the given ID exists.
    """
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    evict_session_agents(session_id)
    connection_manager.disconnect(session_id)
    return {"data": {"message": "Session disconnected successfully."}, "error": None}


@router.get(
    "/connections/{session_id}/tables",
    response_model=ApiResponse,
    summary="List tables in a session",
    description="Returns the names of all usable tables available in the connected database.",
)
def get_tables(session_id: str) -> dict:
    """
    List table names for an active session.

    Parameters
    ----------
    session_id:
        UUID string of the target session.

    Raises
    ------
    HTTPException 404
        If no active session with the given ID exists.
    """
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return {"data": connection_manager.get_tables(session_id), "error": None}


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=ApiResponse,
    summary="Run a natural-language query (non-streaming)",
    description=(
        "Sends the question to the LangGraph SQL agent and waits for a complete "
        "response.  Returns the final answer and the full list of reasoning steps.  "
        "Use ``/query/stream`` for a streaming SSE response."
    ),
)
async def query(req: QueryRequest) -> dict:
    """
    Execute a natural-language query and return the full result.

    The agent internally calls LangChain's SQL toolkit to explore the schema,
    generate and validate SQL, execute the query, and synthesise a plain-English
    answer.

    Parameters
    ----------
    req:
        ``QueryRequest`` containing the ``session_id``, ``question``, and optional
        ``model`` override.

    Returns
    -------
    dict
        ApiResponse envelope where ``data`` contains:
        ``session_id`` (str), ``answer`` (str), ``steps`` (list[QueryStep]).

    Raises
    ------
    HTTPException 404
        If the session does not exist.
    HTTPException 500
        If the agent raises an unexpected error during execution.
    """
    if not connection_manager.is_connected(req.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{req.session_id}' not found.  Connect to a database first.",
        )
    db = connection_manager.get_db(req.session_id)
    meta = connection_manager.get_metadata(req.session_id)
    schema_ddl: str | None = meta.get("schema_ddl") if meta else None
    try:
        result = await run_query(
            db, req.question, req.model, req.provider,
            session_id=req.session_id, schema_ddl=schema_ddl,
        )
        return {
            "data": {
                "session_id": req.session_id,
                "answer": result["answer"],
                "steps": result["steps"],
            },
            "error": None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/query/stream",
    summary="Run a natural-language query (Server-Sent Events)",
    description=(
        "Streams the agent's response as Server-Sent Events.  "
        "Each event is a JSON object with a ``type`` field:\n\n"
        "- ``token``      – partial LLM output; accumulate to build the final answer\n"
        "- ``tool_start`` – the agent is about to call a database tool\n"
        "- ``tool_end``   – the tool returned; includes a truncated ``output``\n"
        "- ``done``       – signals end of stream\n"
        "- ``error``      – an error occurred; ``content`` holds the message\n\n"
        "The ``Content-Type`` header of the response is ``text/event-stream``."
    ),
)
async def query_stream(req: QueryRequest) -> StreamingResponse:
    """
    Stream a natural-language query response via SSE.

    Parameters
    ----------
    req:
        ``QueryRequest`` containing ``session_id``, ``question``, and optional
        ``model`` override.

    Returns
    -------
    StreamingResponse
        A ``text/event-stream`` response.  Each line is prefixed with ``data: ``
        and contains a JSON-encoded event dict.

    Raises
    ------
    HTTPException 404
        If the session does not exist.
    """
    if not connection_manager.is_connected(req.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{req.session_id}' not found.",
        )

    db = connection_manager.get_db(req.session_id)
    meta = connection_manager.get_metadata(req.session_id)
    schema_ddl: str | None = meta.get("schema_ddl") if meta else None

    async def event_generator() -> AsyncGenerator[str, None]:
        """Yield SSE-formatted strings for each agent event."""
        try:
            async for event in stream_query(
                db, req.question, req.model, req.provider,
                session_id=req.session_id, schema_ddl=schema_ddl,
            ):
                yield f"data: {json.dumps(event)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:  # noqa: BLE001
            # Send only the first line so internal details (e.g. system prompt
            # text inside TypeError messages) are never exposed to the client.
            first_line = str(exc).splitlines()[0][:200]
            yield f"data: {json.dumps({'type': 'error', 'content': first_line})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Visualization endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/visualize",
    response_model=ApiResponse,
    summary="Execute SQL and render a chart",
    description=(
        "Runs the given SQL SELECT statement and renders a Plotly chart locally "
        "using the provided chart specification.  Returns a base64-encoded PNG "
        "image that can be embedded directly in an ``<img>`` tag."
    ),
)
async def visualize(req: ChartRequest) -> dict:
    """Execute SQL then render a chart; return base64 PNG."""
    if not connection_manager.is_connected(req.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{req.session_id}' not found.",
        )

    try:
        validate_read_only(req.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = connection_manager.get_db(req.session_id)
    try:
        from sqlalchemy import text as sa_text
        with db._engine.connect() as conn:
            result  = conn.execute(sa_text(req.sql))
            columns = list(result.keys())
            rows    = [list(r) for r in result.fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {str(exc)[:300]}") from exc

    try:
        from app.services.viz_service import render_chart, spec_from_dict
        spec  = spec_from_dict(req.chart_spec)
        loop  = asyncio.get_event_loop()
        b64   = await loop.run_in_executor(
            None,
            lambda: render_chart(spec, rows, columns, req.width, req.height),
        )
        return {"data": {"chart_b64": b64, "columns": columns, "row_count": len(rows)}, "error": None}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Render error: {str(exc)[:300]}") from exc


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------

def _execute_export_sql(session_id: str, sql: str) -> tuple[list, list[str]]:
    """Re-run SQL for export; returns (rows, columns).  Raises HTTPException on failure."""
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    try:
        validate_read_only(sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = connection_manager.get_db(session_id)
    try:
        from sqlalchemy import text as sa_text
        with db._engine.connect() as conn:
            result  = conn.execute(sa_text(sql))
            columns = list(result.keys())
            rows    = [list(r) for r in result.fetchall()]
        return rows, columns
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SQL error: {str(exc)[:300]}") from exc


@router.post(
    "/export/csv",
    summary="Export query results as CSV",
    description="Executes the given SQL and returns a UTF-8 CSV file.",
)
async def export_csv(req: ExportRequest) -> Response:
    rows, columns = _execute_export_sql(req.session_id, req.sql)
    from app.services.export_service import export_csv as _csv
    data = await asyncio.get_event_loop().run_in_executor(None, lambda: _csv(rows, columns))
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{req.title}.csv"'},
    )


@router.post(
    "/export/excel",
    summary="Export query results as Excel (.xlsx)",
    description=(
        "Executes the given SQL and returns a styled Excel workbook.  "
        "If *chart_b64* is provided it is embedded on a second sheet."
    ),
)
async def export_excel(req: ExportRequest) -> Response:
    rows, columns = _execute_export_sql(req.session_id, req.sql)
    from app.services.export_service import export_excel as _excel
    data = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _excel(rows, columns, chart_b64=req.chart_b64, chart_title=req.title),
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{req.title}.xlsx"'},
    )


@router.post(
    "/export/pdf",
    summary="Export query results as PDF",
    description=(
        "Executes the given SQL and returns a landscape-A4 PDF report.  "
        "If *chart_b64* is provided it is appended on a second page."
    ),
)
async def export_pdf(req: ExportRequest) -> Response:
    rows, columns = _execute_export_sql(req.session_id, req.sql)
    from app.services.export_service import export_pdf as _pdf
    data = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _pdf(rows, columns, title=req.title, chart_b64=req.chart_b64),
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{req.title}.pdf"'},
    )
