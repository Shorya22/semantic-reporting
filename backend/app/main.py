"""
FastAPI application entry point for the DataLens AI service.

Mounts:
  * ``/api/v1/*``  — REST surface (connections, query/stream, exports,
                      conversations, preferences)
  * ``/mcp/*``     — MCP server sharing the same connection_manager singleton
  * ``/health``    — liveness probe

Startup tasks:
  * Ensure the upload directory exists
  * Initialise the application metadata DB (creates tables on first run)
  * Warm up the cache (Redis connection probe) — graceful fallback to
    in-memory if Redis is not reachable
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.conversation_routes import router as conversation_router
from app.api.routes import router
from app.cache import cache
from app.config import settings
from app.db.app_db import init_db
from app.mcp.server import mcp as _mcp_server


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("datalens")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup tasks before the server begins accepting traffic."""
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.data_dir, exist_ok=True)

    init_db()
    logger.info("App DB ready at %s", settings.app_db_url)

    backend_choice = (settings.cache_backend or "redis").lower()
    if cache.healthy and getattr(cache, "_is_fake", False):
        logger.info("Cache: fakeredis active (in-process Redis-protocol server).")
    elif cache.healthy:
        logger.info("Cache: Redis connected at %s.", settings.redis_url)
    elif backend_choice == "memory":
        logger.info("Cache: in-memory only (CACHE_BACKEND=memory).")
    else:
        logger.info("Cache: in-memory fallback active (Redis unreachable).")

    yield

    cache.shutdown()


# ---------------------------------------------------------------------------
# OpenAPI tag metadata — drives the section grouping shown in /docs
# ---------------------------------------------------------------------------

OPENAPI_TAGS = [
    {
        "name": "Meta",
        "description": (
            "Liveness, server configuration, and runtime status. "
            "Use these to verify the backend is up and discover the active model/provider defaults."
        ),
    },
    {
        "name": "Models",
        "description": (
            "Discovery endpoints for LLM models. The Ollama list is proxied from the local "
            "Ollama server and cached for 60 seconds to avoid hammering it on every keystroke."
        ),
    },
    {
        "name": "Connections",
        "description": (
            "Open, list, inspect, and close live database sessions. Every query and export "
            "is bound to a `session_id` returned from one of the `POST /connections/*` calls. "
            "Three source types are supported:\n\n"
            "* **SQLite** — file path on the server filesystem\n"
            "* **PostgreSQL** — host / port / db / user / password\n"
            "* **CSV / Excel** — `multipart/form-data` upload, loaded into in-memory SQLite"
        ),
    },
    {
        "name": "Query",
        "description": (
            "Run a natural-language question against a connected database. The SQL agent "
            "uses LangGraph + Groq (or Ollama) to autonomously generate and execute SQL, "
            "then return an answer plus charts and tables.\n\n"
            "Two flavours:\n"
            "* `POST /query` — blocking, returns full result (best for cURL / scripts)\n"
            "* `POST /query/stream` — Server-Sent Events, real-time tokens (best for UIs)"
        ),
    },
    {
        "name": "Visualization",
        "description": (
            "Server-side Plotly chart rendering. Pass a SQL `SELECT` plus a chart spec and "
            "receive a base64 PNG. Used for Excel/PDF exports — the live UI uses the "
            "ECharts JSON option streamed by `/query/stream` instead."
        ),
    },
    {
        "name": "Exports",
        "description": (
            "Download query results as CSV, Excel (`.xlsx`), or landscape A4 PDF. "
            "Excel and PDF can optionally embed a base64 PNG chart on a second sheet/page."
        ),
    },
    {
        "name": "Reports",
        "description": (
            "**Multi-agent report generation.** A single endpoint that runs the entire "
            "Intent → Planner → SQL Workers → Viz Designer → Insight pipeline and returns "
            "a fully-composed PDF (cover, KPI strip, charts, tables, insights, SQL appendix) "
            "or Excel workbook (multi-sheet, native Excel charts, styled summary)."
        ),
    },
    {
        "name": "Conversations",
        "description": (
            "Persistent conversation threads. Each conversation groups a sequence of "
            "user prompts and the corresponding assistant replies (with charts, tables, "
            "tool steps, token usage, and latency). Conversations survive server restarts."
        ),
    },
    {
        "name": "Preferences",
        "description": (
            "Singleton user preferences row. Stores the active model/provider/connection/"
            "conversation so the UI can rehydrate on browser refresh."
        ),
    },
]


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

API_DESCRIPTION = """
## DataLens AI — Natural-Language SQL Analytics

Connect any relational database (or upload a spreadsheet), then ask questions
in plain English. A LangGraph ReAct agent powered by Groq Cloud or local Ollama
generates SQL, runs it, and returns answers with **interactive charts**, **data
tables**, and **CSV / Excel / PDF exports**.

---

### Quick-start (cURL)

```bash
# 1. Open a session against a SQLite file
curl -X POST http://localhost:8000/api/v1/connections/sqlite \\
  -H "Content-Type: application/json" \\
  -d '{"db_path": "D:/semantic-reporting/backend/expenses.db"}'

# Response → {"data": {"session_id": "<uuid>", ...}}

# 2. Ask a question (blocking, full result)
curl -X POST http://localhost:8000/api/v1/query \\
  -H "Content-Type: application/json" \\
  -d '{"session_id": "<uuid>", "question": "Top 5 categories by total"}'
```

### Streaming (Server-Sent Events)

`POST /api/v1/query/stream` returns `text/event-stream`. Each line begins with
`data: ` and is a JSON object — see the endpoint's documentation for every
event type.

### Response envelope

Every JSON endpoint returns:

```json
{ "data": <payload>, "error": null }
```

On error, `data` is `null` and `error` (or FastAPI's `detail`) holds the message.

### Authentication

The API is currently **unauthenticated** — intended for trusted local / network
deployments. Add a reverse proxy or auth middleware before exposing it to the
public internet.
"""


app = FastAPI(
    title="DataLens AI",
    summary="Natural-language SQL analytics API — agentic NL → SQL → charts → exports.",
    description=API_DESCRIPTION,
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=OPENAPI_TAGS,
    contact={
        "name":  "DataLens AI",
        "email": "support@datalens.ai",
    },
    license_info={
        "name": "MIT",
        "url":  "https://opensource.org/license/mit/",
    },
    servers=[
        {"url": "http://localhost:8000", "description": "Local development server"},
    ],
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # CRA / other dev servers
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
# Each router defines its own per-endpoint tags so /docs groups them by
# functional area (Connections, Query, Conversations, …).

app.include_router(router)
app.include_router(conversation_router)

# Mount the MCP server as an ASGI sub-application.
# When accessed via HTTP transport (http://localhost:8000/mcp), the MCP tools
# share the same connection_manager singleton as the REST API — sessions are
# fully interchangeable between the two interfaces.
app.mount("/mcp", _mcp_server.http_app())


@app.get(
    "/health",
    tags=["Meta"],
    summary="Liveness probe",
    description=(
        "Returns HTTP 200 when the service is running. The body reports which cache backend "
        "is currently active (`redis` when reachable, `in-memory` after fallback)."
    ),
    responses={
        200: {
            "description": "Service is healthy.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "service": "datalens-ai",
                        "cache_backend": "in-memory",
                    }
                }
            },
        },
    },
)
def health() -> dict[str, object]:
    """Liveness probe — always returns HTTP 200."""
    if cache.healthy and getattr(cache, "_is_fake", False):
        backend_label = "fakeredis"
    elif cache.healthy:
        backend_label = "redis"
    else:
        backend_label = "in-memory"
    return {
        "status": "ok",
        "service": "datalens-ai",
        "cache_backend": backend_label,
    }
