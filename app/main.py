"""
FastAPI application entry point for the NL-DB Query service.

Application layout
------------------
All API routes are registered under the ``/api/v1`` prefix and defined in
``app.api.routes``.  A single ``/health`` endpoint is added at the root for
liveness checks.

CORS
----
The middleware allows requests from the Vite dev server (port 5173) and the
CRA dev server (port 3000).  In production, replace the origin list with the
actual frontend domain.

Startup
-------
The ``lifespan`` context manager creates the file-upload directory
(``settings.upload_dir``) before the server starts accepting requests.

Running
-------
.. code-block:: bash

    .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.mcp.server import mcp as _mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.

    Creates required directories on startup and performs any cleanup on
    shutdown.  Currently only ensures the upload directory exists.

    Parameters
    ----------
    app:
        The FastAPI application instance (unused but required by the signature).

    Yields
    ------
    None
        Control is yielded to the application; cleanup runs after the yield.
    """
    os.makedirs(settings.upload_dir, exist_ok=True)
    yield
    # Graceful shutdown: nothing to clean up for now.


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NL-DB Query",
    description=(
        "Query any relational database using natural language.  "
        "Connect a SQLite file, a PostgreSQL database, or upload a CSV / Excel "
        "sheet, then ask questions in plain English.  The backend uses a "
        "LangGraph ReAct agent with Groq LLMs to generate and execute SQL "
        "automatically."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
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

app.include_router(router, prefix="/api/v1", tags=["nldb"])

# Mount the MCP server as an ASGI sub-application.
# When accessed via HTTP transport (http://localhost:8000/mcp), the MCP tools
# share the same connection_manager singleton as the REST API — sessions are
# fully interchangeable between the two interfaces.
app.mount("/mcp", _mcp_server.http_app())


@app.get(
    "/health",
    tags=["meta"],
    summary="Health check",
    description="Returns ``{status: 'ok'}`` when the service is running.  Used by load balancers and monitoring.",
)
def health() -> dict[str, str]:
    """Liveness probe — always returns HTTP 200 with ``status: ok``."""
    return {"status": "ok", "service": "nldb-query"}
