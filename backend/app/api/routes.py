"""
FastAPI route definitions for the DataLens AI API.

All routes are mounted under the ``/api/v1`` prefix (configured here on the
APIRouter). Handlers follow the thin-controller pattern: validate → delegate
→ respond. Business logic lives in ``app.db.manager`` (connection management)
and ``app.agents.sql_agent`` (LangGraph agent execution).

Tag groupings (drive the section ordering in ``/docs``):
  * **Meta**           – /config
  * **Models**         – /ollama/models
  * **Connections**    – /connections/*
  * **Query**          – /query, /query/stream
  * **Visualization**  – /visualize
  * **Exports**        – /export/{csv,excel,pdf}
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

from app.agents.llm_factory import llm_for
from app.agents.orchestrator import run_orchestrator
from app.agents.sql_agent import evict_session_agents, run_query, stream_query
from app.api.schemas import (
    ApiResponse,
    ChartRequest,
    ExportRequest,
    PostgresConnectRequest,
    QueryRequest,
    ReportRequest,
    SQLiteConnectRequest,
)
from app.cache import cache
from app.config import settings
from app.db.manager import connection_manager
from app.security.sql_guard import validate_read_only
from app.services.conversation_service import (
    append_assistant_message,
    append_user_message,
    get_or_create_conversation,
    new_message_id,
)

router = APIRouter(prefix="/api/v1")


_OLLAMA_MODELS_KEY = "ollama:models"


# =============================================================================
# Reusable response examples
# =============================================================================

ERR_404_SESSION = {
    "description": "Session not found.",
    "content": {
        "application/json": {
            "example": {"detail": "Session 'b3f0…' not found.  Connect to a database first."}
        }
    },
}

ERR_400_BAD_SQL = {
    "description": "SQL was rejected (write/DDL operation, multi-statement, or invalid SQL).",
    "content": {
        "application/json": {
            "example": {
                "detail": (
                    "SQL contains a blocked operation (Update). "
                    "Only read operations are permitted (SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
                )
            }
        }
    },
}

ERR_422_VALIDATION = {
    "description": "Pydantic validation error — body is missing required fields or has wrong types.",
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {
                        "type":  "string_type",
                        "loc":   ["body", "session_id"],
                        "msg":   "Input should be a valid string",
                        "input": None,
                    }
                ]
            }
        }
    },
}


# =============================================================================
# Meta
# =============================================================================


@router.get(
    "/config",
    tags=["Meta"],
    response_model=ApiResponse,
    summary="Server configuration",
    description=(
        "Returns the runtime configuration the frontend needs to render the model "
        "picker and the provider toggle."
    ),
    responses={
        200: {
            "description": "Configuration returned.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "default_model":   "llama-3.3-70b-versatile",
                            "llm_provider":    "groq",
                            "ollama_base_url": "http://localhost:11434",
                        },
                        "error": None,
                    }
                }
            },
        }
    },
)
def get_config() -> dict:
    """Return public server configuration."""
    return {
        "data": {
            "default_model":   settings.default_model,
            "llm_provider":    settings.llm_provider,
            "ollama_base_url": settings.ollama_base_url,
        },
        "error": None,
    }


# =============================================================================
# Models (Ollama discovery)
# =============================================================================


@router.get(
    "/ollama/models",
    tags=["Models"],
    response_model=ApiResponse,
    summary="List Ollama models",
    description=(
        "Queries the local Ollama server (`OLLAMA_BASE_URL`) and returns the list of "
        "downloaded models. Cached for `CACHE_OLLAMA_TTL` seconds (default 60s) so the "
        "model picker can poll cheaply."
    ),
    responses={
        200: {
            "description": "Ollama is reachable; model list returned.",
            "content": {
                "application/json": {
                    "example": {
                        "data": [
                            {"id": "llama3.2:latest",      "label": "llama3.2:latest"},
                            {"id": "qwen2.5-coder:7b",     "label": "qwen2.5-coder:7b"},
                            {"id": "mistral:7b-instruct",  "label": "mistral:7b-instruct"},
                        ],
                        "error": None,
                    }
                }
            },
        },
        503: {
            "description": "Ollama not reachable at the configured base URL.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Ollama not reachable at http://localhost:11434: Connection refused"
                    }
                }
            },
        },
    },
)
async def get_ollama_models() -> dict:
    """Fetch the model list from the Ollama server, cached for short TTL."""
    cached = await cache.aget(_OLLAMA_MODELS_KEY)
    if cached is not None:
        return {"data": cached, "error": None}

    def _fetch() -> list:
        url = f"{settings.ollama_base_url}/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read())
        return [{"id": m["name"], "label": m["name"]} for m in data.get("models", [])]

    try:
        models = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        await cache.aset(_OLLAMA_MODELS_KEY, models, ttl=settings.cache_ollama_ttl)
        return {"data": models, "error": None}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama not reachable at {settings.ollama_base_url}: {str(exc)[:200]}",
        ) from exc


# =============================================================================
# Connections
# =============================================================================


@router.post(
    "/connections/sqlite",
    tags=["Connections"],
    response_model=ApiResponse,
    status_code=200,
    summary="Connect to a SQLite database",
    description=(
        "Opens a SQLAlchemy connection to a local `.db` / `.sqlite` file and registers "
        "a session. The returned `session_id` is required for every subsequent query, "
        "visualisation, or export request."
    ),
    responses={
        200: {
            "description": "Connection opened.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                            "type":       "sqlite",
                            "path":       "D:/semantic-reporting/backend/expenses.db",
                            "name":       "expenses.db",
                            "tables":     ["transactions", "categories", "accounts"],
                            "schema_ddl": "CREATE TABLE transactions ( ... );",
                        },
                        "error": None,
                    }
                }
            },
        },
        400: {
            "description": "SQLAlchemy could not open the file (wrong format, locked, etc.).",
            "content": {
                "application/json": {
                    "example": {"detail": "file is not a database"}
                }
            },
        },
        404: {
            "description": "File does not exist at the given path.",
            "content": {
                "application/json": {
                    "example": {"detail": "SQLite file not found: D:/missing.db"}
                }
            },
        },
        422: ERR_422_VALIDATION,
    },
)
def connect_sqlite(req: SQLiteConnectRequest) -> dict:
    """Open a connection to a SQLite database file."""
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
    tags=["Connections"],
    response_model=ApiResponse,
    status_code=200,
    summary="Connect to a PostgreSQL database",
    description=(
        "Opens a SQLAlchemy connection to a remote PostgreSQL database. The credentials "
        "are used to build a connection URI of the form "
        "`postgresql://<user>:<password>@<host>:<port>/<database>`."
    ),
    responses={
        200: {
            "description": "Connection opened.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "session_id": "2d4e6c8a-1f3b-4a5e-9c7d-0b8e2a6f4d10",
                            "type":       "postgresql",
                            "host":       "localhost",
                            "port":       5432,
                            "database":   "sales_db",
                            "name":       "sales_db",
                            "tables":     ["customers", "orders", "products"],
                            "schema_ddl": "CREATE TABLE customers ( ... );",
                        },
                        "error": None,
                    }
                }
            },
        },
        400: {
            "description": "Connection failed (bad credentials, unreachable host, etc.).",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "(psycopg2.OperationalError) FATAL:  password authentication failed for user \"postgres\""
                        )
                    }
                }
            },
        },
        422: ERR_422_VALIDATION,
    },
)
def connect_postgres(req: PostgresConnectRequest) -> dict:
    """Open a connection to a PostgreSQL database."""
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
    tags=["Connections"],
    response_model=ApiResponse,
    status_code=200,
    summary="Upload a CSV or Excel file",
    description=(
        "Accepts a `multipart/form-data` upload of a `.csv`, `.xlsx`, or `.xls` file. "
        "The file is parsed with pandas and inserted into a brand-new in-memory SQLite "
        "database so the SQL agent can query it.\n\n"
        "* CSV → 1 table named after the file stem\n"
        "* Excel → 1 table per sheet (sheet name lowercased, spaces → `_`)\n\n"
        "The in-memory engine is held open for the lifetime of the session — closing "
        "the session releases the memory."
    ),
    responses={
        200: {
            "description": "File parsed and session created.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "session_id": "f0a8e4b2-9c7d-4f3e-8b2a-1d5c7f9e0b34",
                            "type":       "csv",
                            "file":       "expenses.csv",
                            "name":       "expenses.csv",
                            "table":      "expenses",
                            "rows":       1547,
                            "columns":    ["id", "category", "amount", "date"],
                            "tables":     ["expenses"],
                            "schema_ddl": "CREATE TABLE expenses ( ... );",
                        },
                        "error": None,
                    }
                }
            },
        },
        400: {
            "description": "Unsupported extension or pandas could not parse the file.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Unsupported file type '.txt'.  Only .csv, .xlsx, and .xls are accepted."
                    }
                }
            },
        },
    },
)
async def upload_file(
    file: UploadFile = File(..., description="CSV (.csv) or Excel (.xlsx / .xls) file."),
) -> dict:
    """Upload a CSV or Excel file and create an in-memory database session."""
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
    tags=["Connections"],
    response_model=ApiResponse,
    summary="List active database sessions",
    description="Returns metadata for every currently active in-memory session.",
    responses={
        200: {
            "description": "List of active sessions (may be empty).",
            "content": {
                "application/json": {
                    "example": {
                        "data": [
                            {
                                "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                                "type":       "sqlite",
                                "name":       "expenses.db",
                                "tables":     ["transactions", "categories"],
                            }
                        ],
                        "error": None,
                    }
                }
            },
        }
    },
)
def list_connections() -> dict:
    """Return metadata for all active sessions."""
    return {"data": connection_manager.list_sessions(), "error": None}


@router.get(
    "/connections/{session_id}",
    tags=["Connections"],
    response_model=ApiResponse,
    summary="Get session metadata",
    description="Returns connection metadata (type, name, tables, schema DDL) for a single session.",
    responses={
        200: {
            "description": "Session found.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                            "type":       "sqlite",
                            "name":       "expenses.db",
                            "tables":     ["transactions", "categories"],
                            "schema_ddl": "CREATE TABLE transactions ( ... );",
                        },
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_SESSION,
    },
)
def get_connection(session_id: str) -> dict:
    """Retrieve metadata for a specific active session."""
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    meta = connection_manager.get_metadata(session_id)
    return {"data": {"session_id": session_id, **meta}, "error": None}


@router.delete(
    "/connections/{session_id}",
    tags=["Connections"],
    response_model=ApiResponse,
    summary="Close a session",
    description=(
        "Disconnects and removes the session from the in-memory registry. For CSV/Excel "
        "sessions this releases the underlying in-memory SQLite engine. The agent graph "
        "cache for this session is also evicted."
    ),
    responses={
        200: {
            "description": "Session closed.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {"message": "Session disconnected successfully."},
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_SESSION,
    },
)
def disconnect(session_id: str) -> dict:
    """Close an active session."""
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    evict_session_agents(session_id)
    connection_manager.disconnect(session_id)
    return {"data": {"message": "Session disconnected successfully."}, "error": None}


@router.get(
    "/connections/{session_id}/example-queries",
    tags=["Connections"],
    response_model=ApiResponse,
    summary="Generate schema-aware example queries for a session",
    description=(
        "Uses the connected database's schema to generate 4 natural-language example "
        "questions the user could ask. Results are cached per session.\n\n"
        "The frontend uses this to populate the empty-state suggestions panel."
    ),
    responses={
        200: {
            "description": "Example queries generated.",
            "content": {
                "application/json": {
                    "example": {
                        "data": [
                            "Show me the top 10 AUAs by transaction volume this month",
                            "What is the overall authentication success rate?",
                            "Give me a monthly trend of KYC transactions",
                            "Compare success rates between FINGER and IRIS biometric modes",
                        ],
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_SESSION,
    },
)
async def get_example_queries(session_id: str) -> dict:
    """Generate 4 schema-aware example questions using the connected DB schema."""
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    cache_key = f"examples:{session_id}"
    cached = await cache.aget(cache_key)
    if cached is not None:
        return {"data": cached, "error": None}

    # Build schema DDL for the prompt
    meta = connection_manager.get_metadata(session_id)
    schema_ddl: str = meta.get("schema_ddl") or ""

    _EXAMPLE_SYSTEM = """\
You are a data analyst. Given a database schema, generate exactly 4 distinct
natural-language questions a business user might ask. The questions should:
- Cover different intent types: 1 single-metric KPI, 1 trend, 1 ranking, 1 comparison or overview
- Be specific to the actual tables and columns in the schema
- Be phrased as a business user would ask (no SQL jargon)
- Be answerable with a SELECT query only

Output ONLY a JSON array of 4 strings. No prose, no markdown, no numbering.
Example: ["How many transactions happened today?", "Show monthly trends", ...]
"""
    _EXAMPLE_USER = f"## Schema\n\n{schema_ddl[:6000]}\n\nGenerate 4 example questions."

    import json as _json
    from langchain_core.messages import HumanMessage, SystemMessage

    examples: list[str] = []
    try:
        llm = llm_for("intent_classifier")  # fast/cheap model is fine here
        resp = await llm.ainvoke([
            SystemMessage(content=_EXAMPLE_SYSTEM),
            HumanMessage(content=_EXAMPLE_USER),
        ])
        raw = str(resp.content).strip()
        # Strip markdown fences if the model wrapped with ```json
        if raw.startswith("```"):
            first_bracket = raw.find("[")
            last_bracket = raw.rfind("]")
            if first_bracket != -1 and last_bracket != -1:
                raw = raw[first_bracket:last_bracket + 1]
        parsed = _json.loads(raw)
        if isinstance(parsed, list):
            examples = [str(q).strip() for q in parsed if str(q).strip()][:6]
    except Exception:
        pass

    # Fallback: generic examples that work for most databases
    if len(examples) < 2:
        examples = [
            "How many total records are in the database?",
            "Show me the row counts for each table",
            "What are the most recent 10 entries?",
            "Give me a summary overview of the data",
        ]

    await cache.aset(cache_key, examples, ttl=settings.cache_schema_ttl)
    return {"data": examples, "error": None}


@router.get(
    "/connections/{session_id}/tables",
    tags=["Connections"],
    response_model=ApiResponse,
    summary="List tables in a session",
    description="Returns the names of all usable tables in the connected database.",
    responses={
        200: {
            "description": "Table names returned.",
            "content": {
                "application/json": {
                    "example": {
                        "data": ["transactions", "categories", "accounts"],
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_SESSION,
    },
)
def get_tables(session_id: str) -> dict:
    """List table names for an active session."""
    if not connection_manager.is_connected(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return {"data": connection_manager.get_tables(session_id), "error": None}


# =============================================================================
# Query
# =============================================================================


@router.post(
    "/query",
    tags=["Query"],
    response_model=ApiResponse,
    summary="Run a NL query (blocking)",
    description=(
        "Sends the question to the LangGraph SQL agent and **waits** for the full "
        "response. Returns the final answer, the list of reasoning/tool steps, and the "
        "token-usage summary.\n\n"
        "Use `POST /query/stream` for token-by-token streaming via Server-Sent Events."
    ),
    responses={
        200: {
            "description": "Query completed.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                            "answer": (
                                "The top 5 categories are Groceries ($12,400.50), "
                                "Rent ($9,800.00), Transport ($4,250.75), "
                                "Dining ($3,100.10), and Utilities ($2,890.40)."
                            ),
                            "steps": [
                                {
                                    "type":  "tool_call",
                                    "tool":  "execute_sql",
                                    "input": "{'sql': 'SELECT category, SUM(amount) AS total ...'}",
                                },
                                {
                                    "type":   "tool_result",
                                    "tool":   "execute_sql",
                                    "output": "category | total\\nGroceries | 12400.50 ...",
                                },
                            ],
                            "usage": {
                                "input_tokens":  2341,
                                "output_tokens": 187,
                                "total_tokens":  2528,
                                "latency_ms":    4823,
                            },
                        },
                        "error": None,
                    }
                }
            },
        },
        404: ERR_404_SESSION,
        422: ERR_422_VALIDATION,
        500: {
            "description": "Agent raised an unexpected error during execution.",
            "content": {
                "application/json": {
                    "example": {"detail": "Daily token quota exhausted on Groq. ..."}
                }
            },
        },
    },
)
async def query(req: QueryRequest) -> dict:
    """Execute a natural-language query and return the full result."""
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
                "answer":     result["answer"],
                "steps":      result["steps"],
                "usage":      result.get("usage"),
            },
            "error": None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/query/stream",
    tags=["Query"],
    summary="Run a NL query (Server-Sent Events, multi-agent pipeline)",
    description=(
        "Streams the **multi-agent** response as `text/event-stream`. Each line is "
        "`data: <json>\\n\\n`. When `conversation_id` is supplied (or auto-created), "
        "the user prompt and assistant reply are persisted to the conversation thread.\n\n"
        "### Multi-agent pipeline events (in roughly this order)\n\n"
        "| `type` | Meaning |\n"
        "|---|---|\n"
        "| `conversation` | First event — resolved conversation + assistant message IDs |\n"
        "| `intent`       | Intent Classifier output: `{intent, wants_chart, wants_dashboard, wants_export, complexity, …}` |\n"
        "| `plan`         | Planner output: `{title, description, query_count, visual_count, layout}` |\n"
        "| `query_start`  | Per planned query: `{query_id, purpose}` |\n"
        "| `query_done`   | Per planned query: `{query_id, success, rows_count, latency_ms, repaired, error}` |\n"
        "| `viz`          | Per visual: `{visual_id, visual_type, title, kpi?, echarts_option?, table_columns?, table_rows?}` |\n"
        "| `dashboard_layout` | Final layout: `{title, layout, visuals}` for the DashboardCanvas |\n"
        "| `insight`      | Insight Agent: `{headline, executive_summary, key_findings, anomalies, recommendations}` |\n"
        "| `critique`     | Critic (advisory): `{passed, score, issues[]}` |\n"
        "| `usage`        | Per-agent latency breakdown: `{intent_latency_ms, plan_latency_ms, insight_latency_ms, total_elapsed_ms}` |\n"
        "| `done`         | Stream is complete |\n"
        "| `error`        | Something went wrong — `content` holds the message |\n\n"
        "### Backwards-compat events (also emitted for existing chat-style UIs)\n\n"
        "| `type` | Meaning |\n"
        "|---|---|\n"
        "| `token`      | Token of the executive summary — concatenate to render |\n"
        "| `chart_spec` | ECharts JSON option object — render with `echarts-for-react` |\n"
        "| `table_data` | Structured query result `{columns, rows, sql, title}` |\n"
        "| `export_ctx` | Last SQL + session_id — enables CSV/Excel/PDF download buttons |\n\n"
        "### Trivial branches (greeting / help)\n\n"
        "When the Intent Classifier short-circuits the question as `greeting` or `help`, "
        "the stream emits the `intent` event followed by a canned reply via `token` events "
        "and `done`. No planner/SQL/insight/critique events are produced.\n\n"
        "### Example stream excerpt for a dashboard question\n"
        "```\n"
        "data: {\"type\":\"intent\",\"intent\":\"dashboard\",\"complexity\":\"complex\",\"wants_chart\":true,\"wants_dashboard\":true}\n\n"
        "data: {\"type\":\"plan\",\"title\":\"AUA Performance Overview\",\"query_count\":4,\"visual_count\":5}\n\n"
        "data: {\"type\":\"query_start\",\"query_id\":\"q1\",\"purpose\":\"Total transactions\"}\n\n"
        "data: {\"type\":\"query_done\",\"query_id\":\"q1\",\"success\":true,\"rows_count\":1,\"latency_ms\":312}\n\n"
        "data: {\"type\":\"viz\",\"visual_id\":\"v1\",\"visual_type\":\"kpi\",\"title\":\"Total Transactions\",\"kpi\":{\"formatted_value\":\"500K\"}}\n\n"
        "data: {\"type\":\"dashboard_layout\",\"title\":\"AUA Performance Overview\",\"layout\":[…],\"visuals\":[…]}\n\n"
        "data: {\"type\":\"insight\",\"headline\":\"AUAs perform consistently above 92% success rate\",\"key_findings\":[…]}\n\n"
        "data: {\"type\":\"critique\",\"passed\":true,\"score\":0.95,\"issues\":[]}\n\n"
        "data: {\"type\":\"usage\",\"intent_latency_ms\":420,\"plan_latency_ms\":2900,\"insight_latency_ms\":1700,\"total_elapsed_ms\":11800}\n\n"
        "data: {\"type\":\"done\"}\n\n"
        "```"
    ),
    responses={
        200: {
            "description": "SSE stream opened.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        "data: {\"type\":\"token\",\"content\":\"Hello\"}\n\n"
                        "data: {\"type\":\"token\",\"content\":\"!\"}\n\n"
                        "data: {\"type\":\"done\"}\n\n"
                    ),
                }
            },
        },
        404: ERR_404_SESSION,
        422: ERR_422_VALIDATION,
    },
)
async def query_stream(req: QueryRequest) -> StreamingResponse:
    """Stream a NL query response via SSE, persisting both user + assistant turns."""
    if not connection_manager.is_connected(req.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{req.session_id}' not found.",
        )

    db = connection_manager.get_db(req.session_id)
    meta = connection_manager.get_metadata(req.session_id)
    schema_ddl: str | None = meta.get("schema_ddl") if meta else None

    conversation = get_or_create_conversation(
        conversation_id=req.conversation_id,
        question=req.question,
        connection_id=req.session_id,
        model=req.model,
        provider=req.provider,
    )
    conversation_id = conversation["id"] if conversation else None

    user_message: dict | None = None
    assistant_message_id = new_message_id()
    if conversation_id:
        user_message = append_user_message(
            conversation_id=conversation_id,
            question=req.question,
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        answer_parts: list[str] = []
        charts: list[dict] = []
        tables: list[dict] = []
        steps: list[dict] = []
        usage: dict | None = None
        export_sql: str | None = None
        error_msg: str | None = None
        visuals: list[dict] = []
        insight_report: dict | None = None
        critique: dict | None = None

        try:
            if conversation_id:
                yield f"data: {json.dumps({'type': 'conversation', 'conversation_id': conversation_id, 'user_message_id': (user_message or {}).get('id'), 'assistant_message_id': assistant_message_id, 'title': conversation.get('title') if conversation else None})}\n\n"

            # Multi-agent orchestrator emits the new event types
            # (intent / plan / query_* / viz / dashboard_layout / insight /
            # critique) AND the back-compat ones (chart_spec / table_data /
            # token / export_ctx / usage) so existing frontends keep working.
            async for event in run_orchestrator(
                question=req.question,
                db=db,
                session_id=req.session_id,
            ):
                etype = event.get("type")
                if etype == "token":
                    answer_parts.append(event.get("content", ""))
                elif etype == "chart_spec":
                    charts.append({
                        "id":     event.get("id", ""),
                        "option": event.get("option", {}),
                        "title":  event.get("title", ""),
                        "sql":    event.get("sql", ""),
                    })
                elif etype == "table_data":
                    tables.append({
                        "id":      event.get("id", ""),
                        "columns": event.get("columns", []),
                        "rows":    event.get("rows", []),
                        "sql":     event.get("sql", ""),
                        "title":   event.get("title", "Query Result"),
                    })
                elif etype in ("query_start", "query_done"):
                    # Map new agent-step events into the persisted "steps"
                    # so message history shows the multi-agent flow too.
                    steps.append({
                        "type":   etype,
                        "tool":   event.get("query_id"),
                        "input":  event.get("purpose"),
                        "output": (f"rows={event.get('rows_count')} "
                                   f"latency={event.get('latency_ms')}ms"
                                   if etype == "query_done" else None),
                    })
                elif etype == "viz":
                    v = {k: v for k, v in event.items() if k != "type"}
                    visuals.append(v)
                elif etype == "insight":
                    insight_report = {k: v for k, v in event.items() if k != "type"}
                elif etype == "critique":
                    critique = {k: v for k, v in event.items() if k != "type"}
                elif etype == "export_ctx":
                    export_sql = event.get("sql") or export_sql
                elif etype == "usage":
                    usage = {
                        "intent_latency_ms":  event.get("intent_latency_ms", 0),
                        "plan_latency_ms":    event.get("plan_latency_ms", 0),
                        "insight_latency_ms": event.get("insight_latency_ms", 0),
                        "total_elapsed_ms":   event.get("total_elapsed_ms", 0),
                    }

                yield f"data: {json.dumps(event)}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as exc:  # noqa: BLE001
            first_line = str(exc).splitlines()[0][:200]
            error_msg = first_line
            yield f"data: {json.dumps({'type': 'error', 'content': first_line})}\n\n"

        finally:
            if conversation_id:
                try:
                    append_assistant_message(
                        conversation_id=conversation_id,
                        answer="".join(answer_parts),
                        charts=charts,
                        tables=tables,
                        steps=steps,
                        usage=usage,
                        export_sql=export_sql,
                        status="error" if error_msg else "done",
                        error=error_msg,
                        message_id=assistant_message_id,
                        visuals=visuals or None,
                        insight_report=insight_report,
                        critique=critique,
                    )
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =============================================================================
# Visualization
# =============================================================================


@router.post(
    "/visualize",
    tags=["Visualization"],
    response_model=ApiResponse,
    summary="Render a chart from SQL",
    description=(
        "Runs the given SQL `SELECT` and renders a Plotly chart server-side using the "
        "provided spec. Returns a base64-encoded PNG that can be embedded in an "
        "`<img>` tag or in an Excel/PDF export.\n\n"
        "**Supported chart types** (in `chart_spec.chart_type`): `bar`, `horizontal_bar`, "
        "`line`, `area`, `scatter`, `pie`, `donut`, `histogram`, `heatmap`, `treemap`, "
        "`funnel`, `box`, `violin`, `bubble`, `waterfall`, `gauge`, `indicator`."
    ),
    responses={
        200: {
            "description": "Chart rendered.",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "chart_b64": "iVBORw0KGgoAAAANSUhEUgAA…",
                            "columns":   ["category", "total"],
                            "row_count": 5,
                        },
                        "error": None,
                    }
                }
            },
        },
        400: ERR_400_BAD_SQL,
        404: ERR_404_SESSION,
        500: {
            "description": "Renderer failed (Plotly/Kaleido error).",
            "content": {
                "application/json": {
                    "example": {"detail": "Render error: Kaleido subprocess failed"}
                }
            },
        },
    },
)
async def visualize(req: ChartRequest) -> dict:
    """Execute SQL then render a Plotly PNG chart, base64-encoded."""
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


# =============================================================================
# Exports
# =============================================================================

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


_EXPORT_RESPONSES: dict = {
    400: ERR_400_BAD_SQL,
    404: ERR_404_SESSION,
}


@router.post(
    "/export/csv",
    tags=["Exports"],
    summary="Export query results as CSV",
    description="Re-runs the given SQL and returns a UTF-8 CSV file as a `Content-Disposition: attachment` download.",
    responses={
        200: {
            "description": "CSV file returned.",
            "content": {
                "text/csv": {
                    "schema":  {"type": "string", "format": "binary"},
                    "example": "category,total\\nGroceries,12400.50\\nRent,9800.00\\n",
                }
            },
        },
        **_EXPORT_RESPONSES,
    },
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
    tags=["Exports"],
    summary="Export query results as Excel (.xlsx)",
    description=(
        "Re-runs the SQL and returns a styled Excel workbook. If `chart_b64` is "
        "supplied, the PNG is embedded on a second sheet."
    ),
    responses={
        200: {
            "description": "XLSX workbook returned.",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
        },
        **_EXPORT_RESPONSES,
    },
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
    tags=["Exports"],
    summary="Export query results as PDF",
    description=(
        "Re-runs the SQL and returns a landscape-A4 PDF report (data table + optional "
        "embedded chart on a second page when `chart_b64` is supplied)."
    ),
    responses={
        200: {
            "description": "PDF returned.",
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
        },
        **_EXPORT_RESPONSES,
    },
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


# =============================================================================
# Multi-agent reports — POST /report
# =============================================================================


@router.post(
    "/report",
    tags=["Reports"],
    summary="Generate a full multi-agent report (PDF or XLSX)",
    description=(
        "Runs the **complete multi-agent pipeline** end-to-end (Intent -> Planner -> "
        "parallel SQL Workers -> Viz Designer -> Insight) and returns a "
        "production-grade deliverable.\n\n"
        "* `format=pdf`  -> landscape A4 with cover, KPI strip, charts, tables, "
        "insights and SQL appendix\n"
        "* `format=xlsx` -> multi-sheet workbook with native Excel charts and a "
        "styled summary sheet\n\n"
        "Generation typically takes 15-60 seconds depending on plan size and Groq latency."
    ),
    responses={
        200: {
            "description": "Report generated.",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
        404: ERR_404_SESSION,
        422: ERR_422_VALIDATION,
        500: {
            "description": "Pipeline failure (planner / workers / composer).",
            "content": {
                "application/json": {
                    "example": {"detail": "Report generation failed: <reason>"}
                }
            },
        },
    },
)
async def generate_report(req: ReportRequest) -> Response:
    """Run the full multi-agent pipeline and return a PDF/XLSX deliverable."""
    if not connection_manager.is_connected(req.session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Session '{req.session_id}' not found.",
        )

    db = connection_manager.get_db(req.session_id)

    # Lazy imports — keep module import time low and decouple from /query path
    from app.agents.intent_classifier import classify_intent
    from app.agents.planner import plan_analysis
    from app.agents.schema_agent import get_schema_context
    from app.agents.sql_workers import run_planned_queries
    from app.agents.viz_designer import design_all_visuals
    from app.agents.insight_agent import generate_insights
    from app.services.report_service import compose_pdf_report, compose_xlsx_report

    try:
        intent = await classify_intent(req.question)
        # Force dashboard intent for report generation — even if the user
        # phrased the question casually, the report must be a full deliverable.
        intent = intent.model_copy(update={
            "wants_dashboard": True,
            "wants_export": req.format,
            "complexity": "complex" if intent.complexity != "complex" else intent.complexity,
        })

        schema = await get_schema_context(req.session_id, db)
        plan = await plan_analysis(req.question, intent, schema.ddl)
        if req.title:
            plan = plan.model_copy(update={"title": req.title})

        if not plan.queries:
            raise HTTPException(
                status_code=500,
                detail="Planner could not generate any executable queries for this question.",
            )

        results = await run_planned_queries(plan, db, schema, session_id=req.session_id)
        visuals = design_all_visuals(plan.visuals, results)
        insight = await generate_insights(req.question, intent, plan, results)

        loop = asyncio.get_event_loop()
        if req.format == "xlsx":
            data = await loop.run_in_executor(
                None,
                lambda: compose_xlsx_report(
                    question=req.question, plan=plan, results=results,
                    visuals=visuals, insight=insight,
                ),
            )
            filename = f"{(req.title or plan.title or 'report').strip().replace(' ', '_')}.xlsx"
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # PDF (default)
        data = await loop.run_in_executor(
            None,
            lambda: compose_pdf_report(
                question=req.question, plan=plan, results=results,
                visuals=visuals, insight=insight,
            ),
        )
        filename = f"{(req.title or plan.title or 'report').strip().replace(' ', '_')}.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Report generation failed: {str(exc).splitlines()[0][:300]}",
        ) from exc
