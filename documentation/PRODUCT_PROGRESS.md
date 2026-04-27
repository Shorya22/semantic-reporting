# Product Progress — Semantic Reporting (NL-DB Query)

> Last updated: 2026-04-27
> Status: **Active Development — Core Feature Complete**

---

## Summary

Semantic Reporting is a natural-language-to-SQL analytics tool. A user connects a database or uploads a file, asks a question in plain English, and gets a data answer, an interactive chart, and a downloadable report — all without writing SQL.

---

## What Is Built (Completed Features)

### Backend

#### Database Connectivity
- [x] **SQLite connection** — connect to any `.db` / `.sqlite` file by absolute path
- [x] **PostgreSQL connection** — connect via host / port / database / user / password
- [x] **CSV upload** — upload `.csv`, auto-loaded into in-memory SQLite; queryable immediately
- [x] **Excel upload** — upload `.xlsx` / `.xls`; each sheet becomes a separate SQL table
- [x] **Session management** — UUID-based sessions, list/get/delete, in-memory registry
- [x] **Schema introspection** — `schema_ddl` extracted on connect, passed to agent as context

#### AI Agent
- [x] **LangGraph ReAct agent** — autonomous loop: agent_node → tools_node → agent_node
- [x] **Tool: `execute_sql`** — runs read-only SQL, returns structured rows + pipe table for LLM
- [x] **Tool: `generate_chart`** — runs SQL, builds ECharts JSON option, returns via sentinel
- [x] **LLM providers** — Groq Cloud (default) and local Ollama, switchable per request
- [x] **Model selection** — frontend can override model per query; server falls back to `.env` default
- [x] **Streaming (SSE)** — real-time token + tool-step events via `astream_events()`
- [x] **Non-streaming endpoint** — blocking `/query` for programmatic use
- [x] **Rate limit retry** — exponential back-off (3 retries) on Groq 429/529; immediate fail on daily quota
- [x] **Graph cache** — one compiled LangGraph per (session × provider × model), evicted on disconnect
- [x] **Structured data extraction** — sentinel pattern intercepts chart/table JSON from tool results
- [x] **Export context** — `export_ctx` SSE event carries last SQL + session_id to enable download buttons
- [x] **Token + latency telemetry** — every `/query` response and SSE `usage` event carries `input_tokens`, `output_tokens`, `total_tokens`, and `latency_ms` (wall-clock). Persisted on assistant messages.

#### Security
- [x] **SQL read-only guard** — sqlglot AST walk blocks INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, GRANT, REVOKE, MERGE, CALL
- [x] **Multi-statement block** — `;\s*\S` pattern rejects stacked statements
- [x] **Keyword fallback** — regex check if sqlglot parse fails (unsupported dialect)
- [x] **CORS whitelist** — only `localhost:5173` and `localhost:3000`
- [x] **Error sanitisation** — exceptions truncated to first line, max 200 chars, before client response

#### Visualization
- [x] **ECharts JSON builder** — 14 chart types: bar, horizontal_bar, line, area, pie, donut, scatter, funnel, treemap, gauge, histogram, box, `+ fallback bar`
- [x] **Plotly PNG renderer** — 18 chart types via Kaleido, returns base64 PNG (used for `/visualize` and exports)
- [x] **Dark theme** — consistent `#0a0f1e` background, `#6366f1` primary color, Inter font

#### Export
- [x] **CSV export** — re-runs SQL, returns UTF-8 CSV file attachment
- [x] **Excel export** — styled `.xlsx` with optional chart PNG embedded on second sheet
- [x] **PDF export** — landscape A4 PDF with data table + optional chart page via fpdf2

#### Persistence (App Metadata DB)
- [x] **SQLAlchemy 2.0 metadata DB** — `app/db/app_db.py` engine + `session_scope`; SQLite (WAL pragmas) by default, swap to Postgres via `APP_DB_URL`
- [x] **`init_db()` lifespan hook** — idempotent table creation on startup
- [x] **ORM models** — `Connection`, `Conversation`, `Message`, `Preference` (`app/db/models.py`)
- [x] **Repository layer** — `ConnectionRepo`, `ConversationRepo`, `MessageRepo`, `PreferenceRepo` (`app/db/repositories.py`)
- [x] **Conversation persistence** — user prompts + assistant replies (with charts/tables/steps/usage) stored on every `/query/stream` call when bound to a `conversation_id`
- [x] **Conversation REST API** — list/create/get/rename/delete + per-conversation message list
- [x] **Preferences** — singleton row stores active model/provider/connection/conversation, restored on browser refresh
- [x] **Lazy connection rehydration** — `ConnectionManager._rehydrate(session_id)` is called transparently from `is_connected` / `get_db` / `get_metadata`. On the first request after a backend restart, persisted SQLite / Postgres / CSV / Excel rows are re-opened from `connections` (passwords decrypted via Fernet, CSV/Excel files re-read from `upload_path`). No client reconnect required.

#### Cache
- [x] **Three selectable backends via `CACHE_BACKEND`** — `redis` (real Redis at `REDIS_URL`, prod default) / `fakeredis` (pure-Python in-process Redis-protocol server, recommended for dev + tests, shipped in `.env.example`) / `memory` (cachetools only)
- [x] **Two-tier fallthrough** (`app/cache/cache.py`) — every backend (real Redis or fakeredis) falls back to `cachetools.TTLCache` if the client errors
- [x] **Auto-recovery** — opportunistic 30s ping reconnects to real Redis when service comes back; skipped under fakeredis (in-process can't be "down")
- [x] **Sync + async wrappers** — `cache.get/set/delete/delete_prefix` and `aget/aset`
- [x] **Wired endpoint** — `GET /api/v1/ollama/models` cached for 60s (`CACHE_OLLAMA_TTL`)
- [x] **`run.bat` skips Docker Redis startup** when `CACHE_BACKEND=fakeredis` or `memory`, and now reports clearly when Docker Desktop daemon isn't running

#### Crypto
- [x] **Fernet encryption at rest** — Postgres passwords encrypted via `app/security/crypto.py` before being stored in `connections.password_enc`
- [x] **Key resolution** — `APP_SECRET_KEY` (prod) → `backend/data/.secret_key` (auto-generated dev fallback, gitignored)

#### Infrastructure
- [x] **FastAPI app** — CORS, lifespan (creates `uploads/`+`data/`, calls `init_db()`, probes cache), OpenAPI docs at `/docs`
- [x] **Health endpoint** — `GET /health` returns `{"status":"ok","service":"datalens-ai","cache_backend":"redis"|"fakeredis"|"in-memory"}`
- [x] **MCP server** — FastMCP ASGI sub-app mounted at `/mcp` (HTTP transport)
- [x] **File upload handling** — async `aiofiles` write, UUID-prefixed filename, ext validation
- [x] **Config system** — pydantic-settings, all settings from `.env`, no hardcoded values
- [x] **Ollama model listing** — `GET /ollama/models` proxies Ollama `/api/tags` (cached)

---

### Frontend

#### Connection UI
- [x] **Connection panel** — tab UI for SQLite / PostgreSQL / CSV-Excel upload
- [x] **SQLite form** — file path input, connect button, session created on success
- [x] **PostgreSQL form** — host / port / database / user / password fields
- [x] **File upload** — drag-and-drop or file picker, uploads and creates session
- [x] **Session sidebar** — lists all active connections with type icon, click to switch

#### Query & Analysis
- [x] **Query bar** — text input + submit button, disabled while streaming
- [x] **Streaming answer** — tokens rendered in real-time as they arrive from SSE
- [x] **Markdown rendering** — `react-markdown` + `remark-gfm` for formatted answers
- [x] **Agent progress** — tool-call step timeline shown while agent is running
- [x] **Analysis cards** — one card per question, stacked in conversation history

#### Visualization
- [x] **Interactive ECharts cards** — rendered client-side from `chart_spec` SSE events
- [x] **ECharts toolbox** — save-as-image, data zoom, restore (built into ECharts)
- [x] **Data tables** — scrollable, styled HTML table for `table_data` SSE events

#### Export
- [x] **CSV download button** — appears after query; calls `/export/csv`
- [x] **Excel download button** — appears after query; calls `/export/excel`
- [x] **PDF download button** — appears after query; calls `/export/pdf`

#### Model Selection
- [x] **Model picker** — dropdown in header, lists Groq and Ollama models
- [x] **Provider toggle** — switch between Groq and Ollama at runtime

#### Schema Browser
- [x] **Insight panel** — shows connected database name, type, and table list
- [x] **Table list** — clickable table names for reference

#### Telemetry & Persistence (UI surfacing)
- [x] **Per-response usage strip** — `InsightPanel` shows `↑input ↓output tok · latency` (ms or s) on every analysis card
- [x] **Conversation hydration types** — frontend types for `Conversation`, `PersistedMessage`, `UserPreferences`; client methods for list/create/rename/delete and preferences GET/PATCH
- [x] **Conversation linkage** — `AnalysisResult.conversationId` / `messageId` populated from the first `conversation` SSE event so the streamed run can be reconciled with the persisted message on reload
- [x] **Hydration hooks** (`hooks/useHydrate.ts`) — `useHydrate` boots `prefs → connections → conversations → messages` in order; `useConversationSync` re-fetches messages on conversation switch; `usePreferenceSync` debounce-pushes pref changes back to `/preferences`
- [x] **Persisted Zustand prefs** — `model`, `provider`, `activeSessionId`, `activeConversationId` saved to localStorage (`datalens-ai-state` v1) via `zustand/middleware.persist`; large lists are never persisted
- [x] **ChatGPT-style sidebar** (`components/Sidebar.tsx`) — top-pinned **New chat** button + collapsible **Connect database** panel; conversations bucketed into `Today` / `Yesterday` / `Previous 7 days` / `Older` via `groupByDate(updated_at)`; per-row inline rename (Enter to commit, Escape to cancel) and confirm-then-delete; connections rendered in a separate collapsible section beneath

---

## Project Structure (Current)

```
d:\semantic-reporting\
├── backend\         FastAPI + LangGraph + Groq
├── frontend\        React + Vite + ECharts + Zustand
└── documentation\   HLD.md, LLD.md, PRODUCT_PROGRESS.md (this file)
```

---

## Environment (Current)

| Item | Value |
|---|---|
| Python | 3.11.9 (CPython) |
| Package manager | pip (via `requirements.txt`); virtualenv via `python -m venv` |
| Node | v22+ (system) |
| LLM default | `llama-3.3-70b-versatile` via Groq Cloud |
| Backend port | 8000 |
| Frontend port | 5173 |
| App metadata DB | `backend/data/app.db` (SQLite, WAL mode) — switch via `APP_DB_URL` |
| Cache | Redis at `redis://localhost:6379/0` when reachable; in-process TTLCache otherwise |
| Crypto key | `backend/data/.secret_key` (dev) — set `APP_SECRET_KEY` in prod |
| Sample DB | `backend/expenses.db` (SQLite, ~50MB) |

---

## Known Issues / Limitations

| # | Issue | Severity | Notes |
|---|---|---|---|
| 1 | No authentication | Medium | Any client on the network can connect/query |
| 2 | `uploads/` grows unbounded | Low | Uploaded files are never deleted after session ends |
| 3 | Graph cache has no TTL or size limit | Low | Memory grows with unique (session × model) combinations |
| 4 | `box` chart type renders empty data | Low | ECharts boxplot needs pre-computed quartiles; current impl sends empty `data: []` |
| 5 | Ollama streaming tokens not implemented | Low | Ollama provider works but may not emit per-token SSE events |
| 6 | `SYNTHESIS_MAX_TOKENS` env var is a no-op | Info | Legacy field kept for `.env` compatibility; synthesize node was removed |
| 7 | `cache_query_ttl` / `cache_schema_ttl` not yet wired | Info | Cache tier is functional, but only the Ollama model list currently uses it; SQL-result and schema-DDL caches are reserved for future work |
| 8 | Local fallback cache lacks per-key TTL | Info | When Redis is down, all entries share `cachetools.TTLCache(ttl=600)` — fine for the current 60s/300s use cases |

---

## What's Next (Potential Roadmap)

| Priority | Feature | Notes |
|---|---|---|
| High | **User authentication** | JWT login, per-user sessions, per-user Preferences row |
| High | **Upload cleanup** | Delete temp files when the connection is hard-deleted |
| High | **Wire query-result cache** | Use `cache.set/get` with `query_cache_key(connection_id, sql)` and `cache_query_ttl` to short-circuit repeated identical SQL |
| Medium | **Schema-DDL cache** | Cache `connection_manager.get_schema_ddl(session_id)` with `cache_schema_ttl` |
| Medium | **Multi-turn conversation** | Already persisted and replayed in the UI; need backend plumbing to re-feed prior turns into the LangGraph checkpointer keyed on `conversation_id` so the agent has long-term memory |
| Medium | **Dashboard mode** | Arrange multiple charts in a grid, export as one PDF |
| Medium | **Schema auto-suggest** | Suggest table names in QueryBar as user types |
| Low | **Box/violin charts fix** | Pre-compute quartiles server-side for ECharts boxplot |
| Low | **Ollama streaming fix** | Verify per-token SSE events work with ChatOllama streaming mode |
| Low | **Connection test button** | Test DB credentials before saving the session |
| Low | **Rate limit UI** | Show remaining Groq quota in the header |
| Low | **Full Docker Compose** | Bundle `backend + frontend + redis + postgres` in `docker-compose.yml` (currently only Redis is in compose; the rest still runs via `run.bat` or local `npm`/`uvicorn`) |

---

## Changelog

| Date | Change |
|---|---|
| 2026-04-27 | **ChatGPT-style sidebar + lazy connection rehydration + repo-root docker-compose.** `components/Sidebar.tsx` rewritten: New-chat button + collapsible *Connect database* panel pinned to the top, conversations bucketed into Today / Yesterday / Previous 7 days / Older with `groupByDate`, inline rename (Enter / Escape) and confirm-delete on each row, connections moved to a collapsible section below the conversation list. `ConnectionManager._rehydrate()` (called from `is_connected` / `get_db` / `get_metadata`) now lazily re-opens persisted SQLite / Postgres / CSV / Excel sessions from the `connections` table on first hit after a restart — no client-side reconnect needed (closes the previous "Live engine handles still in-process" Known Issue). Repo root now ships `docker-compose.yml` (Redis 7-alpine with healthcheck and named volume) so `docker compose up -d` is enough to switch the cache from in-memory to real Redis; `run.bat` already spins up the same `datalens-redis` container automatically when Docker is available. |
| 2026-04-27 | **`CACHE_BACKEND` setting — fakeredis for dev/test, real Redis for prod.** New `CACHE_BACKEND` env var (`redis` \| `fakeredis` \| `memory`). `fakeredis>=2.27.0` added to `requirements.txt`; the cache layer now branches on `settings.cache_backend` and skips the network recovery probe when running on fakeredis (`_is_fake = True`). `.env.example` defaults to `fakeredis` so devs get zero-setup caching; production overrides to `redis`. `run.bat` skips the Docker Redis startup when `CACHE_BACKEND=fakeredis`/`memory`, and surfaces the actual error when Docker is installed but Docker Desktop daemon isn't running. `/health` now reports `redis` \| `fakeredis` \| `in-memory`. |
| 2026-04-27 | **OpenAPI / Swagger documentation overhaul.** Endpoints regrouped into 8 categories (`Meta`, `Models`, `Connections`, `Query`, `Visualization`, `Exports`, `Conversations`, `Preferences`) with rich tag-level descriptions in `app/main.py`. Every endpoint in `app/api/routes.py` and `app/api/conversation_routes.py` now declares an explicit `tags=[...]` and a `responses=` block with realistic success + error examples (200 / 400 / 404 / 422 / 500 / 503). Pydantic schemas (`SQLiteConnectRequest`, `PostgresConnectRequest`, `QueryRequest`, `ChartRequest`, `ExportRequest`, `ConversationCreate`, `ConversationUpdate`, `PreferenceUpdate`) carry multi-example `json_schema_extra` so Swagger UI's "Try it out" picker shows several realistic payloads. Tested: 19 endpoints discovered, 8 tags, 12/12 smoke tests pass. Browse at `http://localhost:8000/docs` (Swagger) or `/redoc` (ReDoc). |
| 2026-04-27 | **Per-response telemetry — input tokens, output tokens, latency.** `sql_agent.run_query` now wraps `graph.ainvoke` in `time.perf_counter()`, walks AIMessages to sum `usage_metadata`, and returns a `usage` dict (`input_tokens`, `output_tokens`, `total_tokens`, `latency_ms`). `stream_query` measures wall-clock around `astream_events()` and includes `latency_ms` in the final `usage` SSE event. `/api/v1/query` non-streaming response now carries `data.usage`. Frontend `TokenUsage` extended with `latency_ms`; `InsightPanel` shows `↑X ↓Y tok · Zms` (formatted as `… s` ≥ 1000 ms). |
| 2026-04-27 | **App metadata DB + persistence layer.** Introduced `app/db/app_db.py` (SQLAlchemy 2.0 + WAL pragmas), `app/db/models.py` (`Connection`, `Conversation`, `Message`, `Preference`), `app/db/repositories.py`, and `app/services/conversation_service.py`. `init_db()` runs on FastAPI lifespan startup. New REST surface in `app/api/conversation_routes.py` exposes `/conversations` (list/create/get/rename/delete), `/conversations/{id}/messages`, and `/preferences` (GET/PATCH). `/query/stream` now persists user prompts + assistant replies (with charts/tables/steps/usage) when bound to a `conversation_id`. |
| 2026-04-27 | **Two-tier cache (Redis → in-process).** New `app/cache/cache.py` with `redis.Redis` primary tier and `cachetools.TTLCache` fallback. Auto-detect health, opportunistic 30s reconnect ping, sync + async APIs (`get/set/delete/delete_prefix`, `aget/aset`). Wired to `GET /api/v1/ollama/models` (60s TTL). New env vars: `REDIS_URL`, `REDIS_ENABLED`, `CACHE_*_TTL`. `/health` now reports `cache_backend`. |
| 2026-04-27 | **Fernet encryption at rest for credentials.** New `app/security/crypto.py` (Fernet AES-128-CBC + HMAC-SHA256). `ConnectionRepo.upsert` encrypts Postgres passwords into `connections.password_enc`; `get_password` decrypts on demand. Key resolution: `APP_SECRET_KEY` env → auto-generated `backend/data/.secret_key` for dev (gitignored). Production deployments must set `APP_SECRET_KEY`. |
| 2026-04-27 | **Switched dependency management from uv → pip + `requirements.txt`.** `backend/requirements.txt` is now the source of truth; setup is `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`. Added `aiosqlite`, `redis`, `cryptography`, `cachetools`. |
| 2026-04-27 | **Fix: blank screen on short greetings (e.g. "Hi").** Backend `QueryRequest.question` had `min_length=3`, so "Hi" returned HTTP 422 with FastAPI's validation array. Frontend was assigning that array to a `string` error field, causing React to silently fail rendering = blank screen. Fixes: (a) lowered `min_length` to 1 in `backend/app/api/schemas.py`, (b) `frontend/src/api/client.ts` now flattens FastAPI validation arrays into readable `field: message` strings before passing to `onError`. |
| 2026-04-27 | Project restructured: `app/` and backend files moved to `backend/`, `frontend/` stays at root. `documentation/` directory created with HLD, LLD, PRODUCT_PROGRESS. |
| 2026-04-27 | Fixed `.env` format — bare comment lines (no `#`) caused python-dotenv parse failures and pydantic `Extra inputs not permitted` errors on startup. All comment lines now properly prefixed with `#`. |
| 2026-04-27 | Python venv recreated with Python 3.11.9 (was 3.13) using `uv --seed` to include pip for VSCode compatibility. `.vscode/settings.json` created to lock interpreter path to `backend/.venv`. |
| 2026-04-27 | Initial working application: FastAPI backend + React frontend + LangGraph ReAct agent + ECharts interactive charts. All dependencies installed (`uv sync` + `npm install`). Backend health check passing at `http://localhost:8000/health`. |
