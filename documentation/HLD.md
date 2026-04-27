# High-Level Design — Semantic Reporting (NL-DB Query)

> Last updated: 2026-04-27 (rev. token+latency telemetry, persistence, cache, crypto, ChatGPT-style sidebar, lazy connection rehydration)

---

## 1. Product Summary

Semantic Reporting is a natural-language-to-SQL analytics platform. Users connect any relational database (SQLite, PostgreSQL) or upload a spreadsheet (CSV/Excel), ask questions in plain English, and receive data answers, interactive charts, and exportable reports — without writing a single line of SQL.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Browser (User)                             │
│                                                                     │
│   ┌───────────────────────────────────────────────────────────┐     │
│   │              React Frontend  (localhost:5173)             │     │
│   │   ConnectionPanel │ QueryBar │ AnalysisCard │ EChartCard  │     │
│   └────────────────────────┬──────────────────────────────────┘     │
└────────────────────────────│────────────────────────────────────────┘
                             │  HTTP / SSE (REST)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend  (localhost:8000)                  │
│                                                                     │
│   /api/v1/connections/*       →  ConnectionManager (db/manager.py)  │
│   /api/v1/query               →  SQL Agent  (agents/sql_agent.py)   │
│   /api/v1/query/stream        →  SQL Agent  (SSE stream)            │
│   /api/v1/conversations/*     →  ConversationRepo + MessageRepo     │
│   /api/v1/preferences         →  PreferenceRepo                     │
│   /api/v1/visualize           →  VizService                         │
│   /api/v1/export/*            →  ExportService                      │
│   /mcp                        →  FastMCP ASGI sub-app               │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────┐       │
│   │              LangGraph ReAct Agent                      │       │
│   │                                                         │       │
│   │   START → agent_node ──► tools_node ──► agent_node     │       │
│   │                      ↘                                  │       │
│   │                       END                               │       │
│   │   Tools:  execute_sql  │  generate_chart                │       │
│   │   Telemetry: input/output tokens + wall-clock latency   │       │
│   └─────────────────────────────────────────────────────────┘       │
│                                                                     │
│   ┌─────────────┐   ┌─────────────┐   ┌─────────────────────────┐  │
│   │ConnectionMgr│   │ SQLGuard    │   │  LLM Gateway            │  │
│   │ in-memory   │   │ AST-level   │   │  Groq Cloud (default)   │  │
│   │ session dict│   │ read-only   │   │  Ollama (local, opt.)   │  │
│   └─────────────┘   │ validation  │   └─────────────────────────┘  │
│                     └─────────────┘                                 │
│                                                                     │
│   ┌──────────────────────┐   ┌────────────────────────────────┐    │
│   │ App Metadata DB      │   │ Cache (CACHE_BACKEND)          │    │
│   │ (SQLAlchemy 2.0)     │   │   redis     → real Redis       │    │
│   │ • Connection         │   │   fakeredis → in-process srv   │    │
│   │ • Conversation       │   │   memory    → cachetools only  │    │
│   │ • Message            │   │   ↓ all three fall through to  │    │
│   │ • Preference         │   │     cachetools.TTLCache        │    │
│   └──────────────────────┘   └────────────────────────────────┘    │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────┐     │
│   │ Crypto (Fernet AES-128-CBC + HMAC-SHA256)                │     │
│   │ Encrypts Postgres passwords at rest in the metadata DB   │     │
│   └──────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    SQLite file         PostgreSQL         CSV / Excel
    (file-based)        (remote)           (in-memory SQLite)

    ──────────────── App-internal stores ────────────────
       Metadata DB:  SQLite (default) or Postgres (APP_DB_URL)
       Cache:        Redis (REDIS_URL) or in-process TTLCache
```

---

## 3. Component Overview

| Component | Location | Responsibility |
|---|---|---|
| **React Frontend** | `frontend/src/` | UI, SSE stream consumer, ECharts rendering, state management |
| **FastAPI App** | `backend/app/main.py` | ASGI entry, CORS, lifespan (DB init + cache warmup), route registration |
| **API Routes (NL/DB)** | `backend/app/api/routes.py` | Connections, query/stream, visualize, exports — thin controllers |
| **API Routes (Conv)** | `backend/app/api/conversation_routes.py` | Conversations, messages, preferences — persistence-only |
| **Schemas** | `backend/app/api/schemas.py` | Pydantic request/response models |
| **Connection Manager** | `backend/app/db/manager.py` | In-memory session registry for live user-data DB engines |
| **App Metadata DB** | `backend/app/db/app_db.py` | SQLAlchemy 2.0 engine + `session_scope`; SQLite (WAL) by default, Postgres-capable |
| **DB Models** | `backend/app/db/models.py` | `Connection`, `Conversation`, `Message`, `Preference` ORM classes |
| **Repositories** | `backend/app/db/repositories.py` | `ConnectionRepo`, `ConversationRepo`, `MessageRepo`, `PreferenceRepo` |
| **Conversation Service** | `backend/app/services/conversation_service.py` | High-level helpers (`get_or_create_conversation`, `append_*_message`) |
| **SQL Agent** | `backend/app/agents/sql_agent.py` | LangGraph ReAct graph; emits per-query token + latency telemetry |
| **SQL Guard** | `backend/app/security/sql_guard.py` | AST-level read-only enforcement via sqlglot |
| **Crypto** | `backend/app/security/crypto.py` | Fernet AES-128-CBC + HMAC-SHA256 — encrypts Postgres passwords at rest |
| **Cache** | `backend/app/cache/cache.py` | Two-tier: Redis primary + `cachetools.TTLCache` fallback |
| **Viz Service** | `backend/app/services/viz_service.py` | ECharts JSON option builder + Plotly PNG renderer |
| **Export Service** | `backend/app/services/export_service.py` | CSV / Excel / PDF report generation |
| **MCP Server** | `backend/app/mcp/server.py` | FastMCP ASGI sub-app at `/mcp` |
| **Config** | `backend/app/config.py` | Pydantic Settings — loaded from `.env` |

---

## 4. Data Flow

### 4.1 Natural Language Query (Streaming)

```
User types question
        │
        ▼
QueryBar (React) ─── POST /api/v1/query/stream (SSE) ──►
        │
        ▼
routes.py: validate session → get SQLDatabase → call stream_query()
        │
        ▼
sql_agent.py: init LangGraph state → graph.astream_events()
        │
        ├── agent_node: LLM decides next action
        │       │
        │       ├── tool_call: execute_sql(sql, title)
        │       │       └── SQLGuard validates → SQLAlchemy executes
        │       │               └── rows/columns → TABLE_JSON_SENTINEL
        │       │
        │       └── tool_call: generate_chart(sql, chart_type, x_col, y_col)
        │               └── SQLGuard → execute → build_echarts_option()
        │                       └── ECharts JSON → CHART_JSON_SENTINEL
        │
        ├── SSE events emitted:
        │   conversation → resolved conversation_id + assistant_message_id (first event)
        │   token        → streaming answer text
        │   tool_start   → agent declared a tool call
        │   tool_end     → tool returned result
        │   chart_spec   → ECharts option JSON object
        │   table_data   → structured { columns, rows }
        │   export_ctx   → last SQL + session_id
        │   usage        → { input_tokens, output_tokens, total_tokens, latency_ms }
        │   done         → stream complete
        │
        ▼
Frontend processes SSE events:
  - Accumulates tokens → renders markdown answer
  - chart_spec → renders interactive ECharts component
  - table_data → renders DataTable component
  - export_ctx → enables CSV/Excel/PDF download buttons
  - usage      → renders "↑X ↓Y tok · Zms" metadata strip on the analysis card

After the stream ends, the backend persists the assistant message
(answer, charts, tables, steps, usage, export_sql) to the conversations
DB so the conversation can be hydrated on browser reload.
```

### 4.2 Database Connection

```
User picks connection type
        │
        ├── SQLite  → POST /connections/sqlite  → ConnectionManager.connect_sqlite()
        ├── Postgres → POST /connections/postgres → ConnectionManager.connect_postgres()
        └── Upload  → POST /connections/upload   → multipart save → load_csv() / load_excel()
                        └── pandas reads file → in-memory SQLite engine → SQLDatabase
        │
        ▼
session_id (UUID) returned → stored in frontend Zustand store
```

---

## 5. Tech Stack

### Backend
| Layer | Technology | Version |
|---|---|---|
| Web Framework | FastAPI | 0.136+ |
| ASGI Server | Uvicorn | 0.46+ |
| Agent Orchestration | LangGraph | 1.1+ |
| LLM SDK (Groq) | langchain-groq | 1.1+ |
| LLM SDK (Ollama) | langchain-community ChatOllama | 0.4+ |
| Database ORM | SQLAlchemy | 2.0+ |
| Data Processing | pandas | 3.0+ |
| Chart Rendering | Plotly + Kaleido | 6.7+ / 1.2+ |
| SQL Parsing | sqlglot | 30+ |
| MCP Server | FastMCP | 3.2+ |
| Settings | pydantic-settings | 2.14+ |
| Export | fpdf2, openpyxl | 2.8+, 3.1+ |
| App Metadata DB | SQLite (default) / PostgreSQL (via `APP_DB_URL`) | — |
| Async SQLite driver | aiosqlite | 0.20+ |
| Cache (primary) | Redis | 5.2+ (server 7.x) |
| Cache (fallback) | cachetools `TTLCache` | 5.5+ |
| Crypto | cryptography (Fernet) | 44.0+ |
| Package Manager | pip + `requirements.txt` | — |
| Python | CPython 3.11 | 3.11.9 |

### Frontend
| Layer | Technology | Version |
|---|---|---|
| Framework | React | 18.3+ |
| Build Tool | Vite | 6.0+ |
| Language | TypeScript | 5.7+ |
| Styling | Tailwind CSS | 3.4+ |
| Charts | Apache ECharts (via echarts-for-react) | 6.0+ |
| State | Zustand | 5.0+ |
| Markdown | react-markdown + remark-gfm | 9.0+ |
| Icons | lucide-react | 0.468+ |

### LLM Providers
| Provider | Mode | Default Model |
|---|---|---|
| Groq Cloud | Remote API | `llama-3.3-70b-versatile` |
| Ollama | Local server | configurable (llama3.2, mistral, etc.) |

---

## 6. Architecture Decision Records (ADRs)

### ADR-001: LangGraph ReAct Agent (no synthesis node)
The agent's final `AIMessage` is the answer, streamed directly. An earlier design used a separate "synthesize" LLM call to rewrite the answer, which doubled latency and introduced hallucination risk (the synthesizer rewrote numbers). Removed in favour of the agent answering directly from tool results.

### ADR-002: ECharts JSON (not Plotly PNG) for interactive charts
Charts are rendered client-side using Apache ECharts option objects. The backend builds the JSON config and sends it via SSE. This gives users zoom, pan, hover tooltips, and save-as-image — capabilities impossible with server-side PNG rendering. Plotly PNG rendering is retained only for the `/visualize` and export endpoints.

### ADR-003: Sentinel pattern for structured data in tool messages
Tool results are text strings in LangChain's message model. Structured chart/table data is embedded using null-byte sentinels (`\x00CHARTJSON\x00`, `\x00TABLEJSON\x00`). The `tools_node` intercepts and strips these before the LLM sees the message, preventing raw JSON from polluting the LLM context window.

### ADR-004: sqlglot AST walk for SQL injection prevention
SQL validation uses `sqlglot.parse()` + node-type walking rather than regex keyword matching. This catches write operations hidden inside CTEs and subqueries. Regex fallback activates only when sqlglot fails to parse (unsupported dialect edge cases).

### ADR-005: In-memory SQLite for CSV/Excel sessions
Uploaded files are loaded into an in-memory `sqlite://` engine via pandas. The engine reference is kept alive in `ConnectionManager._engines` for the session lifetime. This gives the SQL agent a real database to query — no custom CSV parsing logic needed.

### ADR-006: LLM provider is runtime-selectable per request
Both `model` and `provider` fields are optional on `QueryRequest`. When omitted, the server uses the `.env` defaults. This lets the frontend expose a model picker without requiring a server restart.

### ADR-007: Application metadata DB separate from user data DBs
The application persists its own state (connections, conversations, messages, preferences) in a SQLAlchemy-managed database (`APP_DB_URL`, default SQLite at `backend/data/app.db` with WAL pragma). The user's queryable databases are managed independently by `connection_manager` and never written to. This separation lets us swap the metadata store to Postgres in production without affecting how user data is read.

### ADR-008: Two-tier cache with three selectable backends
The cache layer is selected at boot via `CACHE_BACKEND`:

* **`redis`** (production default) — connects to `REDIS_URL`. On any `RedisError`/`OSError` it flips `_healthy = False` and serves from `cachetools.TTLCache` until a 30-second background ping recovers.
* **`fakeredis`** (dev/test default in `.env.example`) — pure-Python in-process Redis-protocol server. Same client API as real Redis (so the cache code path is exercised, not the fallback), but zero setup — no daemon, no Docker, no network. Always healthy, recovery probe is skipped.
* **`memory`** — skip Redis entirely; only `cachetools.TTLCache`.

This keeps the app fully functional in dev environments without Docker, makes test suites trivial (no Redis fixture management), and resilient to transient Redis outages in production. Used today for the Ollama model list (60s TTL); reserved for query-result and schema-DDL caching.

### ADR-009: Encrypt Postgres passwords at rest with Fernet
Connection rows persisted in the metadata DB include Postgres credentials. Passwords are stored in `password_enc` using `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256). Key resolution order: `APP_SECRET_KEY` env var → auto-generated `backend/data/.secret_key` (gitignored). Production must set `APP_SECRET_KEY` explicitly via secrets manager. Plaintext passwords never leave the encrypt/decrypt boundary.

### ADR-010: Per-query token + latency telemetry on every response
Every `/query` response and the streaming `usage` SSE event carry `input_tokens`, `output_tokens`, `total_tokens`, and `latency_ms` (wall-clock for the whole agent run). Tokens are accumulated from `langchain_core.messages.AIMessage.usage_metadata`; latency is measured around `graph.ainvoke` / `graph.astream_events`. Persisted on the assistant `Message.usage_json` so the analysis card can render the metadata strip after browser reload.

---

## 7. Security Model

- **SQL injection**: All SQL executed by the agent passes through `validate_read_only()` — AST walk blocks INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, GRANT, REVOKE, MERGE, CALL. Multi-statement SQL (`; \S`) is also rejected.
- **File uploads**: Only `.csv`, `.xlsx`, `.xls` extensions accepted. Files are saved with a UUID prefix to avoid path collision.
- **CORS**: Explicit origin whitelist (`localhost:5173`, `localhost:3000`). No wildcard.
- **Secrets**: API key loaded from `.env` via pydantic-settings, never in source code or responses.
- **Encryption at rest**: Postgres passwords in the metadata DB are encrypted with Fernet (`app/security/crypto.py`). The encryption key is sourced from `APP_SECRET_KEY` (production) or auto-generated to `backend/data/.secret_key` (dev only — gitignored).
- **Error sanitisation**: Exception messages truncated to first line (max 200 chars) before returning to client — internal stack traces never exposed.

---

## 8. Scalability Notes (current limitations)

| Concern | Current Approach | Production Path |
|---|---|---|
| Live engine handles | Persistent rows in metadata DB + in-process `connection_manager` cache; `_rehydrate()` lazily re-opens engines from persisted state on first miss after a restart | Multi-worker deployment with a shared engine registry (e.g. Postgres-backed) + per-worker LRU eviction |
| Conversation history | SQLite (WAL) at `backend/data/app.db` | Postgres via `APP_DB_URL` |
| Concurrency | Single uvicorn worker | Multiple workers + shared metadata DB + shared Redis cache |
| Cache | Redis (primary) → in-process TTLCache (fallback) | Already production-shaped; needs Redis HA in real prod |
| File uploads | Local `uploads/` dir | S3 / object storage |
| LLM rate limits | Exponential back-off (3 retries, 5s initial) | Queue + worker pool |
| Graph cache | In-process dict per (session × model × provider) | Eviction + TTL |
| Secret key | File-backed dev fallback at `backend/data/.secret_key` | `APP_SECRET_KEY` env injected from secrets manager |
