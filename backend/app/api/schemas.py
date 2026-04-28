"""
Pydantic request / response schemas for the DataLens AI REST API.

All request models use ``Field(...)`` or ``Field(default)`` with explicit
``description`` values so that the auto-generated OpenAPI docs are
self-explanatory without requiring any extra annotation.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Connection request schemas
# ---------------------------------------------------------------------------


class SQLiteConnectRequest(BaseModel):
    """
    Payload for connecting to a local SQLite database file.

    The server resolves the path to an absolute form, so both absolute and
    relative paths are accepted.  Relative paths are resolved from the working
    directory of the server process.
    """

    db_path: str = Field(
        ...,
        description=(
            "Filesystem path to the SQLite database file, e.g. "
            "'/home/user/data/sales.db' or '../expenses.db'."
        ),
        examples=["/Users/alice/data/expenses.db"],
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID to reuse for this session.  When omitted a new UUID "
            "is generated automatically.  Supply an existing ID to overwrite a "
            "previous session with a new connection."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Connect to local sample DB",
                    "value": {"db_path": "D:/semantic-reporting/backend/expenses.db"},
                },
                {
                    "summary": "Connect with caller-supplied session_id (idempotent reconnect)",
                    "value": {
                        "db_path": "D:/semantic-reporting/backend/expenses.db",
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                    },
                },
            ]
        }
    }


class PostgresConnectRequest(BaseModel):
    """
    Payload for connecting to a PostgreSQL database over the network.

    The credentials are used to build a SQLAlchemy connection URI of the form
    ``postgresql://user:password@host:port/database``.
    """

    host: str = Field(
        ...,
        description="Hostname or IP address of the PostgreSQL server.",
        examples=["localhost", "db.example.com"],
    )
    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="TCP port the PostgreSQL server listens on (default: 5432).",
    )
    database: str = Field(
        ...,
        description="Name of the PostgreSQL database to connect to.",
        examples=["sales_db", "analytics"],
    )
    user: str = Field(
        ...,
        description="PostgreSQL username.",
        examples=["admin", "readonly_user"],
    )
    password: str = Field(
        ...,
        description="Password for the PostgreSQL user.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional UUID to assign to this session.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Local Postgres",
                    "value": {
                        "host": "localhost",
                        "port": 5432,
                        "database": "sales_db",
                        "user": "postgres",
                        "password": "postgres",
                    },
                },
                {
                    "summary": "Remote Postgres with custom port",
                    "value": {
                        "host":     "db.example.com",
                        "port":     5433,
                        "database": "analytics",
                        "user":     "readonly_user",
                        "password": "********",
                    },
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Query request schema
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """
    Payload for submitting a natural-language query against a connected database.

    The ``session_id`` must correspond to an active connection previously created
    via one of the ``/connections/*`` endpoints or the file upload endpoint.
    """

    session_id: str = Field(
        ...,
        description=(
            "UUID of the active database session to query.  Obtained from the "
            "response of any connection endpoint."
        ),
    )
    question: str = Field(
        ...,
        min_length=1,
        description=(
            "Natural-language question about the data, e.g. "
            "'What are the top 5 expense categories by total amount?'"
        ),
        examples=["Show total expenses grouped by category"],
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model ID to use for this query.  When ``null`` the server falls back to "
            "the configured default.  For GroqCloud: 'llama-3.3-70b-versatile', "
            "'llama-3.1-8b-instant', 'mixtral-8x7b-32768', 'gemma2-9b-it'.  "
            "For Ollama: any model name available on the local server (e.g. 'llama3.2')."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "LLM provider override for this request: 'groq' or 'ollama'.  "
            "When ``null`` the server uses the configured default (LLM_PROVIDER in .env)."
        ),
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional conversation thread ID. When supplied, the user message and "
            "assistant reply are persisted to that conversation. When omitted the "
            "request runs ad-hoc without persistence."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Simple aggregation question",
                    "value": {
                        "session_id":      "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "question":        "What are the top 5 expense categories by total amount?",
                        "model":           None,
                        "provider":        None,
                        "conversation_id": None,
                    },
                },
                {
                    "summary": "Greeting (no SQL needed)",
                    "value": {
                        "session_id":      "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "question":        "Hi",
                    },
                },
                {
                    "summary": "Chart-producing question, persisted to a conversation",
                    "value": {
                        "session_id":      "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "conversation_id": "c-9f3c1b2a",
                        "question":        "Show monthly spend as a bar chart",
                        "model":           "llama-3.3-70b-versatile",
                        "provider":        "groq",
                    },
                },
                {
                    "summary": "Use a local Ollama model for this query only",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "question":   "How many transactions are in the table?",
                        "model":      "llama3.2",
                        "provider":   "ollama",
                    },
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Conversation / preference schemas
# ---------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    """Payload for creating a new conversation thread."""

    title: Optional[str] = Field(default=None, description="Display title; auto-generated when omitted.")
    connection_id: Optional[str] = Field(default=None, description="Active DB session this conversation is bound to.")
    model: Optional[str] = Field(default=None, description="Model ID to use for this conversation.")
    provider: Optional[str] = Field(default=None, description="LLM provider for this conversation.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Empty thread (defaults applied)",
                    "value": {},
                },
                {
                    "summary": "Bound to a session, custom title + model",
                    "value": {
                        "title":         "Q1 Sales Analysis",
                        "connection_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "model":         "llama-3.3-70b-versatile",
                        "provider":      "groq",
                    },
                },
            ]
        }
    }


class ConversationUpdate(BaseModel):
    """Payload for renaming or rebinding a conversation."""

    title: Optional[str] = None
    connection_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"summary": "Rename only",     "value": {"title": "Renamed thread"}},
                {"summary": "Switch model",    "value": {"model": "llama-3.1-8b-instant"}},
                {"summary": "Switch provider", "value": {"provider": "ollama", "model": "llama3.2"}},
            ]
        }
    }


class PreferenceUpdate(BaseModel):
    """Partial update for the singleton user preference row."""

    model: Optional[str] = None
    provider: Optional[str] = None
    active_connection_id: Optional[str] = None
    active_conversation_id: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Set active conversation",
                    "value": {"active_conversation_id": "c-9f3c1b2a"},
                },
                {
                    "summary": "Switch default model + provider",
                    "value": {"model": "llama3.2", "provider": "ollama"},
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Inner response fragments
# ---------------------------------------------------------------------------


class QueryStep(BaseModel):
    """
    A single reasoning or tool-interaction step produced by the SQL agent.

    Consumers can use the ``type`` field to decide how to render each step:
    - ``tool_call``   – the agent decided to call a database tool
    - ``tool_result`` – the tool returned a result
    - ``ai_message``  – an intermediate LLM reasoning message
    """

    type: Literal["tool_call", "tool_result", "ai_message"] = Field(
        ...,
        description=(
            "Step category: 'tool_call' when the agent invoked a tool, "
            "'tool_result' when the tool returned data, "
            "'ai_message' for intermediate LLM reasoning text."
        ),
    )
    tool: Optional[str] = Field(
        default=None,
        description=(
            "Name of the SQL toolkit tool that was called or that returned a result "
            "(e.g. 'sql_db_query', 'sql_db_schema').  ``null`` for 'ai_message' steps."
        ),
    )
    input: Optional[str] = Field(
        default=None,
        description="Stringified arguments passed to the tool.  Present only on 'tool_call' steps.",
    )
    output: Optional[str] = Field(
        default=None,
        description=(
            "Truncated string representation of the tool's return value (max 1 000 chars).  "
            "Present only on 'tool_result' steps."
        ),
    )
    content: Optional[str] = Field(
        default=None,
        description="Intermediate reasoning text from the LLM.  Present only on 'ai_message' steps.",
    )


# ---------------------------------------------------------------------------
# Export / visualization request schemas
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Payload for CSV / Excel / PDF export endpoints."""

    session_id: str = Field(
        ...,
        description="UUID of the active database session.",
    )
    sql: str = Field(
        ...,
        min_length=3,
        description="Complete SQL SELECT statement whose results to export.",
    )
    chart_b64: Optional[str] = Field(
        default=None,
        description="Base64-encoded PNG chart image to embed in Excel/PDF exports.",
    )
    title: str = Field(
        default="Data Report",
        description="Report title used in Excel and PDF exports.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Plain CSV export (no chart)",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "sql":        "SELECT category, SUM(amount) AS total FROM transactions GROUP BY category ORDER BY total DESC",
                        "title":      "Categories by total",
                    },
                },
                {
                    "summary": "Excel/PDF with embedded chart",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "sql":        "SELECT category, SUM(amount) AS total FROM transactions GROUP BY category ORDER BY total DESC",
                        "chart_b64":  "iVBORw0KGgoAAAANSUhEUgAA…",
                        "title":      "Q1 Categories",
                    },
                },
            ]
        }
    }


class ReportRequest(BaseModel):
    """Payload for the /report endpoint — runs the full multi-agent pipeline
    and returns a fully-composed PDF or XLSX deliverable.
    """

    session_id: str = Field(..., description="UUID of the active database session.")
    question: str = Field(
        ..., min_length=1,
        description="Natural-language question that drives the analysis.",
    )
    format: Literal["pdf", "xlsx"] = Field(
        default="pdf",
        description="Output file format. `pdf` = landscape A4 deliverable; `xlsx` = multi-sheet workbook.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Override the report title (defaults to the planner's auto-generated title).",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Generate a PDF dashboard report",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "question":   "Give me a complete performance overview of all AUAs",
                        "format":     "pdf",
                    },
                },
                {
                    "summary": "Generate an Excel report with native charts",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "question":   "Show monthly KYC volume by KYC type",
                        "format":     "xlsx",
                    },
                },
            ]
        }
    }


class ChartRequest(BaseModel):
    """Payload for the /visualize endpoint."""

    session_id: str = Field(..., description="UUID of the active database session.")
    sql: str = Field(..., min_length=3, description="SQL SELECT statement to execute.")
    chart_spec: dict = Field(
        ...,
        description=(
            "Chart specification dict.  Required key: ``chart_type`` "
            "(bar | line | area | scatter | pie | donut | histogram | heatmap | "
            "treemap | funnel | box | violin | bubble | waterfall | gauge).  "
            "Optional: title, x, y, color, aggregation, sort, limit."
        ),
    )
    width:  int = Field(default=900,  ge=200, le=3000, description="Image width in pixels.")
    height: int = Field(default=500,  ge=200, le=3000, description="Image height in pixels.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Bar chart of categories by total",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "sql":        "SELECT category, SUM(amount) AS total FROM transactions GROUP BY category ORDER BY total DESC LIMIT 10",
                        "chart_spec": {
                            "chart_type": "bar",
                            "title":      "Top 10 categories",
                            "x":          "category",
                            "y":          "total",
                            "sort":       "desc",
                            "limit":      10,
                        },
                        "width":  900,
                        "height": 500,
                    },
                },
                {
                    "summary": "Line chart of monthly trend",
                    "value": {
                        "session_id": "8f3c1b2a-9e6d-4d8f-9c45-1b9a7e3f0c12",
                        "sql":        "SELECT strftime('%Y-%m', date) AS month, SUM(amount) AS total FROM transactions GROUP BY month ORDER BY month",
                        "chart_spec": {
                            "chart_type": "line",
                            "title":      "Monthly spend",
                            "x":          "month",
                            "y":          "total",
                        },
                    },
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class ApiResponse(BaseModel):
    """
    Standard response envelope used by every endpoint.

    Either ``data`` is populated (on success) or ``error`` is populated (on
    failure).  Both fields are never ``null`` at the same time.
    """

    data: Any = Field(
        default=None,
        description="Response payload on success.  Shape varies by endpoint.",
    )
    error: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable error message when the request could not be completed.  "
            "``null`` on success."
        ),
    )
