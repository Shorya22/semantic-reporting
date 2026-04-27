"""
FastMCP server — thin database tools, fully synced with the REST API layer.

Architecture
------------
All tools delegate to the **shared** ``connection_manager`` singleton from
``app.db.manager``.  This means:

- When the MCP server is mounted inside the FastAPI process (``/mcp`` route),
  sessions created via the REST API are immediately visible to MCP tools and
  vice versa — they share the same in-memory registry.

- When the MCP server is run as a standalone stdio subprocess (Claude Desktop
  ``command`` mode), it gets its own process and therefore its own session
  registry — but the **full tool surface** is identical to the REST API:
  SQLite, PostgreSQL, CSV, and Excel are all supported.

Tools exposed
-------------
connect_sqlite    – open a local .db / .sqlite file
connect_postgres  – connect to a remote PostgreSQL server
load_csv          – load a CSV file into an in-memory SQLite database
load_excel        – load an Excel workbook into an in-memory SQLite database
list_sessions     – list all active database sessions
list_tables       – list table names in a session
get_schema        – full DDL for all tables (or a specific table)
execute_sql       – run any SQL statement, returns plain-text results
disconnect        – close a session and release resources

Transport modes
---------------
HTTP (same process as FastAPI — fully shared sessions):
    Mounted at /mcp in app/main.py.
    Claude Desktop or any HTTP MCP client can connect at:
      http://localhost:8000/mcp

Stdio (Claude Desktop subprocess — process-local sessions):
    Add to claude_desktop_config.json:

    {
      "mcpServers": {
        "nldb-query": {
          "command": "/path/to/.venv/bin/python",
          "args": ["-m", "app.mcp.server"],
          "cwd": "/path/to/basic_mcp"
        }
      }
    }
"""

import os
from typing import Optional

from fastmcp import FastMCP

from app.db.manager import connection_manager
from app.security.sql_guard import validate_read_only

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "nldb-query",
    instructions=(
        "Natural Language Database Query server.\n\n"
        "Workflow:\n"
        "1. Connect with connect_sqlite / connect_postgres / load_csv / load_excel.\n"
        "2. Call get_schema to understand the tables and columns.\n"
        "3. Write SQL based on the user's question.\n"
        "4. Call execute_sql with your SQL.\n"
        "5. Interpret the results and answer the user.\n\n"
        "You are the reasoning engine — the tools are just database I/O.\n\n"
        "Session management:\n"
        "- Every connect/load call returns a session_id and sets it as the active session.\n"
        "- All subsequent tool calls use the active session automatically.\n"
        "- Pass session_id explicitly to target a specific session.\n"
        "- Call list_sessions to see all open connections."
    ),
)

# Tracks the most recently connected session so callers can omit session_id.
_active_session: Optional[str] = None


# ---------------------------------------------------------------------------
# Connection tools
# ---------------------------------------------------------------------------


@mcp.tool()
def connect_sqlite(db_path: str, session_id: str = "") -> str:
    """
    Connect to a local SQLite database file.

    Opens the file, registers a session, and returns a schema summary so you
    can immediately understand what tables and columns are available.

    Parameters
    ----------
    db_path:
        Absolute path to the ``.db`` or ``.sqlite`` file.
        Example: ``/Users/alice/data/expenses.db``
    session_id:
        Optional UUID to assign to this session.  Auto-generated when empty.
    """
    global _active_session

    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        return f"Error: file not found at '{abs_path}'. Provide an absolute path."

    try:
        sid = connection_manager.connect_sqlite(abs_path, session_id or None)
        _active_session = sid
        meta = connection_manager.get_metadata(sid)
        tables = meta["tables"] if meta else []
        schema = meta.get("schema_ddl", "") if meta else ""
        table_list = ", ".join(tables) if tables else "(no tables found)"
        return (
            f"Connected to '{os.path.basename(abs_path)}'.\n"
            f"Session: {sid}\n"
            f"Tables: {table_list}\n\n"
            f"Schema:\n{schema}"
        )
    except Exception as exc:
        return f"Error connecting: {exc}"


@mcp.tool()
def connect_postgres(
    host: str,
    database: str,
    user: str,
    password: str,
    port: int = 5432,
    session_id: str = "",
) -> str:
    """
    Connect to a remote PostgreSQL database.

    Parameters
    ----------
    host:       Hostname or IP address of the PostgreSQL server.
    database:   Name of the database to connect to.
    user:       PostgreSQL username.
    password:   PostgreSQL password.
    port:       TCP port (default 5432).
    session_id: Optional UUID (auto-generated when empty).
    """
    global _active_session

    try:
        sid = connection_manager.connect_postgres(
            host, port, database, user, password, session_id or None
        )
        _active_session = sid
        meta = connection_manager.get_metadata(sid)
        tables = meta["tables"] if meta else []
        schema = meta.get("schema_ddl", "") if meta else ""
        table_list = ", ".join(tables) if tables else "(no tables found)"
        return (
            f"Connected to PostgreSQL '{database}' on {host}:{port}.\n"
            f"Session: {sid}\n"
            f"Tables: {table_list}\n\n"
            f"Schema:\n{schema}"
        )
    except Exception as exc:
        return f"Error connecting to PostgreSQL: {exc}"


@mcp.tool()
def load_csv(file_path: str, table_name: str = "", session_id: str = "") -> str:
    """
    Load a CSV file into an in-memory SQLite database.

    The file is parsed with pandas and inserted into a single table.  All
    columns are available for SQL queries immediately after loading.

    Parameters
    ----------
    file_path:  Absolute path to the ``.csv`` file.
    table_name: Table name to use.  Defaults to the file stem (lowercased,
                spaces replaced with underscores).
    session_id: Optional UUID (auto-generated when empty).
    """
    global _active_session

    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return f"Error: file not found at '{abs_path}'."

    try:
        sid = connection_manager.load_csv(abs_path, table_name or None, session_id or None)
        _active_session = sid
        meta = connection_manager.get_metadata(sid)
        rows = meta.get("rows", "?") if meta else "?"
        cols = meta.get("columns", []) if meta else []
        tbl = meta.get("table", "?") if meta else "?"
        schema = meta.get("schema_ddl", "") if meta else ""
        return (
            f"Loaded '{os.path.basename(abs_path)}' → table '{tbl}'.\n"
            f"Session: {sid}\n"
            f"Rows: {rows}  |  Columns ({len(cols)}): {', '.join(str(c) for c in cols)}\n\n"
            f"Schema:\n{schema}"
        )
    except Exception as exc:
        return f"Error loading CSV: {exc}"


@mcp.tool()
def load_excel(file_path: str, session_id: str = "") -> str:
    """
    Load an Excel workbook into an in-memory SQLite database.

    Every sheet in the workbook becomes a separate SQL table (sheet name
    lowercased, spaces replaced with underscores).

    Parameters
    ----------
    file_path:  Absolute path to the ``.xlsx`` or ``.xls`` file.
    session_id: Optional UUID (auto-generated when empty).
    """
    global _active_session

    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return f"Error: file not found at '{abs_path}'."

    try:
        sid = connection_manager.load_excel(abs_path, session_id or None)
        _active_session = sid
        meta = connection_manager.get_metadata(sid)
        sheets = meta.get("sheets", []) if meta else []
        tables = meta.get("tables", []) if meta else []
        schema = meta.get("schema_ddl", "") if meta else ""
        mapping = "  |  ".join(
            f"{s} → {t}" for s, t in zip(sheets, tables)
        ) or "(none)"
        return (
            f"Loaded '{os.path.basename(abs_path)}'.\n"
            f"Session: {sid}\n"
            f"Sheets → Tables: {mapping}\n\n"
            f"Schema:\n{schema}"
        )
    except Exception as exc:
        return f"Error loading Excel: {exc}"


# ---------------------------------------------------------------------------
# Session management tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_sessions() -> str:
    """
    List all active database sessions with their type, name, and tables.

    Marks the currently active session (the one used when session_id is
    omitted from other tool calls).
    """
    sessions = connection_manager.list_sessions()
    if not sessions:
        return "No active sessions. Connect with connect_sqlite / connect_postgres / load_csv / load_excel."
    lines = [f"Active sessions ({len(sessions)}):"]
    for s in sessions:
        sid = s["session_id"]
        marker = " ← active" if sid == _active_session else ""
        tables = ", ".join(s.get("tables", [])) or "(none)"
        lines.append(
            f"  [{s['type']}]  {s['name']}  |  session: {sid}  |  tables: {tables}{marker}"
        )
    return "\n".join(lines)


@mcp.tool()
def list_tables(session_id: str = "") -> list[str]:
    """
    List all table names in the connected database.

    Parameters
    ----------
    session_id:
        Optional session UUID.  Uses the active session when empty.

    Returns
    -------
    list[str]
        Table names, or an empty list if no session is active.
    """
    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return []
    return connection_manager.get_tables(sid)


@mcp.tool()
def get_schema(table_name: str = "", session_id: str = "") -> str:
    """
    Return the schema (CREATE TABLE DDL + sample rows) for one or all tables.

    Call this before writing SQL so you know the exact column names, types,
    and relationships.

    Parameters
    ----------
    table_name:
        Name of a specific table, e.g. ``"expenses"``.
        Leave empty to get the schema for ALL tables.
    session_id:
        Optional session UUID.  Uses the active session when empty.
    """
    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return (
            "Error: no database connected. "
            "Call connect_sqlite / connect_postgres / load_csv / load_excel first."
        )

    db = connection_manager.get_db(sid)
    try:
        if table_name:
            return db.get_table_info([table_name])
        return db.get_table_info()
    except Exception as exc:
        return f"Error retrieving schema: {exc}"


@mcp.tool()
def execute_sql(query: str, session_id: str = "") -> str:
    """
    Execute a SQL statement and return the results as plain text.

    This is the core tool — write correct SQL based on the schema, call this,
    and read the results.  If the query errors, the error message is returned
    so you can diagnose and retry with a corrected statement.

    Parameters
    ----------
    query:
        A complete, executable SQL statement.
        Examples:
          ``SELECT * FROM expenses ORDER BY amount DESC LIMIT 10``
          ``SELECT category, SUM(amount) FROM expenses GROUP BY category``
    session_id:
        Optional session UUID.  Uses the active session when empty.

    Returns
    -------
    str
        Query results as plain text, ``"(no rows returned)"`` for empty
        results, or an error description if the statement failed.
    """
    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return (
            "Error: no database connected. "
            "Call connect_sqlite / connect_postgres / load_csv / load_excel first."
        )

    db = connection_manager.get_db(sid)
    try:
        raw = db.run(query)
        return str(raw) if raw else "(no rows returned)"
    except Exception as exc:
        return f"SQL Error: {exc}"


@mcp.tool()
def disconnect(session_id: str = "") -> str:
    """
    Close the active database session and release its resources.

    Parameters
    ----------
    session_id:
        Optional session UUID.  Closes the active session when empty.

    Returns
    -------
    str
        Confirmation or error message.
    """
    global _active_session

    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return "No active session to disconnect."

    try:
        connection_manager.disconnect(sid)
        if _active_session == sid:
            _active_session = None
        return f"Session {sid[:8]}… disconnected."
    except Exception as exc:
        return f"Error disconnecting: {exc}"


# ---------------------------------------------------------------------------
# Visualization tools
# ---------------------------------------------------------------------------


@mcp.tool()
def generate_chart(
    sql: str,
    chart_type: str,
    title: str = "",
    x: str = "",
    y: str = "",
    color: str = "",
    aggregation: str = "",
    sort: str = "",
    limit: int = 0,
    width: int = 900,
    height: int = 500,
    session_id: str = "",
) -> str:
    """
    Execute a SQL query and render a chart locally.  Returns a base64-encoded
    PNG string that can be embedded in an <img> tag or saved to a file.

    Parameters
    ----------
    sql:         Complete SELECT statement to execute.
    chart_type:  bar | horizontal_bar | line | area | scatter | pie | donut |
                 histogram | heatmap | treemap | funnel | box | violin |
                 bubble | waterfall | gauge | indicator
    title:       Chart title.
    x:           Column name for the x-axis / category dimension.
    y:           Column name for the y-axis / value dimension.
    color:       Column name used for color grouping.
    aggregation: sum | count | avg | max | min (optional pre-render aggregation).
    sort:        asc | desc
    limit:       Max data points to render (0 = all).
    width:       Image width in pixels (default 900).
    height:      Image height in pixels (default 500).
    session_id:  Optional session UUID.  Uses the active session when empty.
    """
    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return "Error: no database connected. Connect first."

    try:
        validate_read_only(sql)
    except ValueError as exc:
        return f"SQL guard error: {exc}"

    db = connection_manager.get_db(sid)
    try:
        from sqlalchemy import text as sa_text
        with db._engine.connect() as conn:
            result  = conn.execute(sa_text(sql))
            columns = list(result.keys())
            rows    = [list(r) for r in result.fetchall()]
    except Exception as exc:
        return f"SQL Error: {exc}"

    try:
        from app.services.viz_service import ChartSpec, render_chart
        spec = ChartSpec(
            chart_type=chart_type, title=title, x=x, y=y, color=color,
            aggregation=aggregation, sort=sort, limit=limit,
        )
        b64 = render_chart(spec, rows, columns, width=width, height=height)
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        return f"Render error: {exc}"


@mcp.tool()
def create_dashboard(
    panels_json: str,
    title: str = "Dashboard",
    cols: int = 2,
    panel_width: int = 800,
    panel_height: int = 420,
    session_id: str = "",
) -> str:
    """
    Render multiple SQL result sets as a professional grid dashboard.

    Parameters
    ----------
    panels_json:  JSON array of panel objects.  Each panel:
                  {
                    "sql": "SELECT ...",
                    "chart_type": "bar",
                    "title": "...",
                    "x": "col_name",
                    "y": "col_name"
                  }
    title:        Dashboard title.
    cols:         Number of grid columns (default 2).
    panel_width:  Width of each panel in pixels.
    panel_height: Height of each panel in pixels.
    session_id:   Optional session UUID.

    Returns
    -------
    str
        ``data:image/png;base64,<...>`` data URI of the full dashboard image.
    """
    import json as _json_mod
    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return "Error: no database connected. Connect first."

    try:
        panel_defs = _json_mod.loads(panels_json)
    except Exception:
        return "Error: panels_json must be a valid JSON array."

    db = connection_manager.get_db(sid)
    panels = []
    for pd_item in panel_defs:
        sql = pd_item.get("sql", "")
        if not sql:
            continue
        try:
            validate_read_only(sql)
            from sqlalchemy import text as sa_text
            with db._engine.connect() as conn:
                result  = conn.execute(sa_text(sql))
                columns = list(result.keys())
                rows    = [list(r) for r in result.fetchall()]
        except Exception as exc:
            return f"Error in panel SQL: {exc}"

        from app.services.viz_service import spec_from_dict
        spec = spec_from_dict(pd_item)
        panels.append((spec, rows, columns))

    if not panels:
        return "Error: no valid panels provided."

    try:
        from app.services.viz_service import render_dashboard
        b64 = render_dashboard(panels, title=title, cols=cols,
                               panel_width=panel_width, panel_height=panel_height)
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        return f"Dashboard render error: {exc}"


# ---------------------------------------------------------------------------
# Export tools
# ---------------------------------------------------------------------------


@mcp.tool()
def export_data(
    sql: str,
    format: str = "csv",
    title: str = "export",
    chart_b64: str = "",
    session_id: str = "",
) -> str:
    """
    Execute a SQL query and export the results.

    Parameters
    ----------
    sql:        Complete SELECT statement.
    format:     ``csv`` | ``excel`` | ``pdf``
    title:      Filename stem and report title (no extension).
    chart_b64:  Optional base64 PNG to embed in Excel/PDF exports.
    session_id: Optional session UUID.

    Returns
    -------
    str
        ``data:<mime>;base64,<...>`` data URI of the exported file.
        Decode and save with the appropriate extension (.csv / .xlsx / .pdf).
    """
    import base64 as _b64

    sid = session_id or _active_session
    if not sid or not connection_manager.is_connected(sid):
        return "Error: no database connected. Connect first."

    try:
        validate_read_only(sql)
    except ValueError as exc:
        return f"SQL guard error: {exc}"

    db = connection_manager.get_db(sid)
    try:
        from sqlalchemy import text as sa_text
        with db._engine.connect() as conn:
            result  = conn.execute(sa_text(sql))
            columns = list(result.keys())
            rows    = [list(r) for r in result.fetchall()]
    except Exception as exc:
        return f"SQL Error: {exc}"

    fmt = format.lower().strip()
    try:
        if fmt == "csv":
            from app.services.export_service import export_csv
            data = export_csv(rows, columns)
            mime = "text/csv"
        elif fmt in ("excel", "xlsx"):
            from app.services.export_service import export_excel
            data = export_excel(rows, columns, chart_b64=chart_b64 or None, chart_title=title)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            from app.services.export_service import export_pdf
            data = export_pdf(rows, columns, title=title, chart_b64=chart_b64 or None)
            mime = "application/pdf"
        else:
            return f"Error: unsupported format '{format}'.  Use csv, excel, or pdf."

        return f"data:{mime};base64,{_b64.b64encode(data).decode()}"
    except Exception as exc:
        return f"Export error: {exc}"


# ---------------------------------------------------------------------------
# Entry point — stdio transport for Claude Desktop
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
