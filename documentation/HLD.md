# High-Level Design — Semantic Reporting (NL-DB Query)

> Last updated: 2026-04-28 (rev. structured output across all agents, two-layer critic, orchestrator re-plan + clarification routing, DataFacts single-computation, example-queries endpoint)

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
│   │       Multi-Agent Pipeline  (app/agents/orchestrator)   │       │
│   │                                                         │       │
│   │   1. classify_intent  → greeting/help short-circuit     │       │
│   │                          (dynamic reply using schema)   │       │
│   │                          low-confidence → clarification │       │
│   │   2. schema_agent     → cached DDL + column profiles    │       │
│   │      (fetched before trivial branches so greeting       │       │
│   │       replies can include real table names)             │       │
│   │   3. planner          → AnalysisPlan (queries+visuals)  │       │
│   │   4. sql_workers      → asyncio fan-out, real-time SSE  │       │
│   │                          Strategy A: LLM column fix     │       │
│   │                          Strategy B: LLM full rewrite   │       │
│   │      >50% fail → re-plan with error context (once)      │       │
│   │   5. viz_designer     → ECharts / KPI / table builder   │       │
│   │   6. insight_agent    → DataFacts-grounded narrative    │       │
│   │   7. critic           → two-layer quality gate          │       │
│   │      Layer 1: programmatic number check (zero tokens)   │       │
│   │      Layer 2: LLM semantic check (label / direction)    │       │
│   │      DataFacts computed ONCE, shared by 6 + 7           │       │
│   │      Critic errors → insight retry (max 2 times)        │       │
│   │                                                         │       │
│   │   All agents use with_structured_output(Model)          │       │
│   │   Token tracking via contextvars accumulator (_usage.py)│       │
│   │   Telemetry: per-agent + total latency, tokens, queries │       │
│   └─────────────────────────────────────────────────────────┘       │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────┐       │
│   │       Security — defence-in-depth read-only             │       │
│   │   1. Input guardrails (Python regex)                    │       │
│   │   2. Agent system prompts (LLM topic filter)            │       │
│   │   3. SQL guard (sqlglot AST walk)                       │       │
│   │   4. Engine-level read-only                             │       │
│   │      • SQLite : PRAGMA query_only=ON                    │       │
│   │      • PG     : default_transaction_read_only=on        │       │
│   │   5. UI affordances (READ-ONLY badge, refusal copy)     │       │
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
| **Orchestrator** | `backend/app/agents/orchestrator.py` | Drives the 7-agent pipeline, emits SSE events at each stage |
| **Intent Classifier** | `backend/app/agents/intent_classifier.py` | First gate — labels question (greeting/help/simple_qa/metric/exploration/dashboard/report/comparison), short-circuits trivial cases without LLM |
| **Schema Agent** | `backend/app/agents/schema_agent.py` | Cached DDL + lightweight per-column profile (cardinality, sample values, ranges) for the Planner |
| **Planner** | `backend/app/agents/planner.py` | Decomposes the question into a typed `AnalysisPlan` (parallel queries + planned visuals + 12-col grid layout) |
| **SQL Workers** | `backend/app/agents/sql_workers.py` | Execute every `PlannedQuery` concurrently via `asyncio.create_task`; two LLM-driven repair strategies on failure (Strategy A: column fix, Strategy B: full rewrite); results cached; each strategy re-validates read-only before execution |
| **Viz Designer** | `backend/app/agents/viz_designer.py` | Deterministic builder turning `(PlannedVisual, QueryResult)` pairs into KPI / chart / table payloads |
| **Insight Agent** | `backend/app/agents/insight_agent.py` | Anti-hallucination executive narrative: never sees raw rows — receives `DataFacts` (verified min/max/sum/avg/top-N) computed from ALL rows; `InsightReport` output via `with_structured_output`; `latency_ms` excluded from LLM schema |
| **Critic** | `backend/app/agents/critic.py` | Two-layer quality gate: Layer 1 programmatic (number extraction, year skip, K/M tolerance, empty-result check — zero token cost); Layer 2 LLM semantic (mis-labels, unaddressed intent, direction errors — cannot override Layer 1 verdicts); never blocks delivery |
| **LLM Factory** | `backend/app/agents/llm_factory.py` | `llm_for(agent_name)` resolves model/provider/temp/max_tokens with per-call override priority |
| **Usage Accumulator** | `backend/app/agents/_usage.py` | `contextvars.ContextVar`-based per-pipeline token accumulator; `start_bucket()` resets at pipeline start, `record(response)` called in each agent after LLM call, `totals()` read at pipeline end for the `usage` SSE event |
| **SQL Agent (legacy)** | `backend/app/agents/sql_agent.py` | Original LangGraph ReAct agent — retained for `POST /query` (non-streaming) |
| **Connection Manager** | `backend/app/db/manager.py` | In-memory session registry for live user-data DB engines + DB-level read-only enforcement (PRAGMA / Postgres options) |
| **Input Guardrails** | `backend/app/security/guardrails.py` | Pre-LLM check for prompt-injection patterns, destructive intent, and obvious off-topic prompts |
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

### 4.1 Natural Language Query (Streaming, multi-agent pipeline)

```
User types question
        │
        ▼
QueryBar (React) ─── POST /api/v1/query/stream (SSE) ──►
        │
        ▼
routes.py: validate session → resolve/create conversation → call run_orchestrator()
        │
        ▼
orchestrator.py: multi-stage pipeline, emits SSE events at each stage
        │
        ├── 1. Input guardrail (regex)              → may emit `error` and stop
        ├── 2. classify_intent                       emits  `intent`
        │
        ├── 3. get_schema_context (cached in Redis)  ← fetched BEFORE trivial branches
        │     so greeting/help replies include real table names
        │
        ├── 4. Trivial branch checks (no SQL, no LLM for planner):
        │     greeting → dynamic reply with table names, emit `done`, return
        │     help     → dynamic reply with DB summary, emit `done`, return
        │     confidence < 0.35 → clarification prompt, emit `done`, return
        │
        ├── 5. plan_analysis                         emits  `plan`
        │     (no queries → emit one apology token + `done`, return)
        │
        ├── 6. sql_workers (asyncio fan-out)         emits  `query_start` × N
        │       └── per query:                              `query_done`   × N
        │           SQLGuard AST walk → engine read-only
        │           Strategy A: LLM column/table name fix
        │           Strategy B: LLM full rewrite from purpose + schema
        │           each strategy re-validates read-only before exec
        │           successful rows cached for cache_query_ttl
        │
        ├── 6b. Re-plan if >50% queries failed       emits  `plan` (replan=true)
        │       new plan executed; adopted only if it reduces failure count
        │
        ├── 7. design_visual (deterministic)         emits  `viz`               × M
        │       └── back-compat events for chat UI:        `chart_spec`        × M
        │                                                   `table_data`        × M
        │       └── after all visuals:                      `dashboard_layout`
        │
        ├── 8. DataFacts computed ONCE from ALL result rows
        │     (passed to both insight_agent and critic — no redundant computation)
        │
        ├── 9. generate_insights (DataFacts-grounded) emits  `insight`
        │       └── streams executive_summary as      `token` × N
        │
        ├── 10. critic feedback loop (up to 2 retries):
        │       Layer 1: programmatic number check (zero tokens)
        │       Layer 2: LLM semantic review (receives Layer 1 verdicts)
        │       error issues → regenerate insight with corrective feedback
        │       final:                                emits  `critique`
        │
        ├── 11. accounting                           emits  `usage`
        │     {intent_latency_ms, plan_latency_ms,
        │      insight_latency_ms, total_elapsed_ms,
        │      input_tokens, output_tokens, total_tokens}
        │
        └── 12. final                                emits  `done`
        │
        ▼
Frontend processes SSE events:
  - intent / plan        → AgentProgress timeline (which agent is running)
  - query_start/done     → per-query progress badges
  - viz / dashboard_layout → DashboardCanvas (12-col grid of KPI / chart / table)
  - chart_spec / table_data → legacy chat-style cards (still rendered)
  - token                → streaming markdown into InsightSection
  - insight              → headline, exec summary, findings, anomalies, recos
  - critique             → optional Critic warnings strip below insight
  - export_ctx           → enables CSV/Excel/PDF download buttons
  - usage                → "↑in ↓out tok · Zms" strip + per-agent latency tooltip

After the stream ends, the backend persists the assistant message (answer
text, charts, tables, steps, usage, export_sql) to the conversations DB so
the conversation can be hydrated on browser reload.
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

### ADR-011: Multi-agent pipeline replaces the single ReAct agent for streaming
The streaming endpoint (`POST /query/stream`) routes through `app.agents.orchestrator.run_orchestrator` instead of the legacy LangGraph ReAct agent. Seven specialised agents — intent classifier, schema agent, planner, SQL workers, viz designer, insight agent, critic — each focused on one concern. Why:

* **Cost / latency**: trivial intents (greeting, help) short-circuit *without any LLM call*; "metric" / "simple_qa" skip the dashboard branch; only "dashboard" / "report" engages all 7 agents.
* **Parallelism**: SQL Workers fan out via `asyncio.gather`, so a 6-query dashboard finishes in roughly the time of the slowest query. The single ReAct loop ran them serially.
* **Determinism**: visual rendering and layout are deterministic Python — no LLM hallucinations of column names or chart types. The LLM picks the *plan*, code executes it.
* **Right-sized models**: the cheap llama-3.1-8b is fine for intent and viz design; only Planner and Insight need llama-3.3-70b. We pay 70B prices only where reasoning is needed.
* **Observability**: each stage emits its own SSE event with its own latency, so the frontend can show a per-agent timeline and we can find the slow stage at a glance.

The legacy LangGraph ReAct agent is retained for `POST /query` (non-streaming) so cURL / scripted clients see no change.

### ADR-012: Per-agent LLM config in `.env` (not code)
Each of the seven agent roles has its own `MODEL_*`, `PROVIDER_*`, `MAX_TOKENS_*`, `TEMP_*` pair in `.env`, resolved by `Settings.agent_config(agent_name)`. The factory `app.agents.llm_factory.llm_for(agent_name, override_*)` is the only place `ChatGroq` / `ChatOllama` are instantiated. This means a sysadmin can A/B test "what if planner used Sonnet instead?" by changing one env var — no code edit, no redeploy, no model imports leaking into business logic. Per-call overrides from `QueryRequest.model` / `provider` (the user's frontend selector) take priority so a single user can experiment without affecting others.

### ADR-013: Defence-in-depth read-only enforcement (5 layers)
Application-internal data is the user's data; we must never let a malicious or hallucinated SQL statement modify it. Five independent layers, cheapest first:

1. **Input guardrail** (`app/security/guardrails.py`) — regex check before any LLM call rejects prompt-injection patterns, natural-language destructive intent, and obvious off-topic prompts.
2. **Agent system prompts** — each LLM is told it is read-only; the system prompt is the second filter.
3. **SQL guard** (`app/security/sql_guard.py`) — sqlglot AST walk on every executed statement blocks DDL/DCL/DML and stacked statements.
4. **Engine-level read-only** (`app/db/manager.py`) — SQLite uses `PRAGMA query_only = ON` attached as a connect listener; Postgres uses `connect_args.options = "-c default_transaction_read_only=on -c statement_timeout=30000"`. Even if all higher layers were bypassed, the database itself refuses writes.
5. **UI affordances** — visible "READ-ONLY" badge on the QueryBar, deterministic refusal copy on guardrail hits.

The earlier the layer, the cheaper a rejection; the later, the harder to bypass. All five run on every request.

### ADR-014: Dashboard layout is server-driven (12-column grid)
The Planner emits a typed `layout: list[LayoutRow]` where each row contains slots `{visual_id, width}` summing to 12. The frontend `DashboardCanvas` renders one CSS-grid row per `LayoutRow` and spans each visual across `slot.width` of 12 columns. This keeps layout decisions where the data is — the agent that knows what the dashboard *means* also knows how it should look — and lets the same plan feed PDF/XLSX export composers without re-asking the LLM.

### ADR-015: Command palette (⌘K) and keyboard-first nav
Slash commands (`/help`, `/clear`, `/new`, `/disconnect`, `/export pdf`, `/model …`, `/provider …`, `/tables`, `/schema`, `/stop`, `/retry`, `/continue`) are matched in `frontend/src/commands.ts` and surfaced via a Command Palette (Ctrl/⌘+K). Same actions are reachable from the Sidebar, so the palette is a power-user shortcut, not a hard dependency. The sidebar itself toggles via Ctrl/⌘+B and the toggle button slides with the seam between sidebar and main pane so it's always reachable.

### ADR-016: `with_structured_output(Model, include_raw=True)` pattern across all agents
All four LLM-calling agents (intent classifier, planner, insight agent, critic) use LangChain's `.with_structured_output(PydanticModel, include_raw=True)`. This returns `{"raw": AIMessage, "parsed": Model | None, "parsing_error": Exception | None}`. The `raw` AIMessage carries `usage_metadata` for token tracking via `_usage.record(result["raw"])`. The `parsed` path is primary; `_parse_json_lenient()` on `raw.content` is the fallback. Pydantic field `description` strings are injected directly into the tool/schema the model sees — they serve as in-prompt guidance without polluting the system prompt. `latency_ms` fields carry `exclude=True` so the LLM never tries to fill them; they are set via `.model_copy(update={"latency_ms": elapsed()})` after the call returns.

### ADR-017: Two-layer critic with deterministic authority separation
The critic has two fully independent layers. Layer 1 is deterministic Python (zero tokens): extracts every cited number with a regex that requires at least one comma group to prevent "2026" matching as "202"; skips year-range integers (1800–2200); builds an allowed set that includes K/M floor and ceiling values for tolerance; runs empty-result detection. Layer 2 is the LLM semantic reviewer: it receives Layer 1's `_build_verified_numbers_block()` as authoritative context and is instructed it CANNOT re-check `✓` numbers — its scope is restricted to mis-labels, unaddressed question parts, and direction errors. This prevents the LLM from overriding a correct number just because it looks unusual. Error issues from both layers can trigger up to `_MAX_INSIGHT_RETRIES = 2` insight regeneration cycles.

### ADR-018: DataFacts single-computation, shared between Insight and Critic
`compute_data_facts(plan, results)` processes ALL rows of ALL successful queries into verified statistics (min, max, sum, avg, all unique values up to 50, top-N categorical counts). This computation is done exactly once per pipeline run in the orchestrator and the resulting `facts` object is passed as `data_facts=facts` to both `generate_insights()` and `critique()`. Neither agent re-derives statistics from raw rows. This guarantees the Insight Agent and Critic work from identical ground truth and eliminates redundant row iteration.

### ADR-019: Dynamic re-planning on mass SQL failure
After the initial SQL fan-out, if `failed_q / total_q > _REPLAN_FAILURE_THRESHOLD (0.50)`, the orchestrator re-calls `plan_analysis()` with the original question amended to include the error messages from all failed queries. If the re-plan produces queries and the re-execution has fewer failures than the original attempt, the re-plan is adopted (new `plan` + `results` replace the originals). Otherwise the original plan proceeds. This handles schema hallucinations and dialect mismatches without user intervention.

### ADR-020: Low-confidence clarification routing
When `intent.confidence < _CLARIFICATION_THRESHOLD (0.35)`, the orchestrator streams a clarification request (no SQL, no LLM for the planner, no token spend for downstream agents) and returns. This prevents the planner from guessing at an ambiguous question, which wastes 70B tokens and typically produces a confusing dashboard. The clarification message suggests metric, time range, and output format as examples of what the user might clarify.

### ADR-021: Schema fetched before trivial-intent branches
`get_schema_context(session_id, db)` is called before the greeting/help/clarification branches rather than after them. The extra call costs nothing for non-trivial intents (schema is Redis-cached at `cache_schema_ttl`, default 1 day). For trivial intents it enables `_greeting_reply(schema)` and `_help_reply(schema)` to include the actual connected table names and database summary, making the reply contextually useful instead of a generic canned string.

### ADR-022: `contextvars`-based pipeline-wide token accumulator
Token counting for the multi-agent pipeline uses a `contextvars.ContextVar[_UsageBucket]` in `app/agents/_usage.py`. Python's `asyncio` automatically propagates `ContextVar` state into spawned `asyncio.Task` objects, so concurrent SQL worker tasks all accumulate into the same per-request bucket without any explicit threading. The orchestrator calls `start_bucket()` once at pipeline start, each agent calls `_record_usage(result["raw"])` after its LLM call, and `totals()` is read at the end for the `usage` SSE event. No function signature changes were needed across the six agents.

### ADR-023: Schema-aware example queries endpoint
`GET /api/v1/connections/{session_id}/example-queries` generates four schema-specific natural-language questions (one KPI, one trend, one ranking, one comparison) using a cheap LLM call with the connected database's DDL as context. Results are cached at `cache_schema_ttl`. The frontend uses this to populate the empty-state suggestion panel. Fallback to four generic questions if the LLM call fails or returns fewer than two results.

---

## 7. Security Model

### 5-layer defence-in-depth read-only

| # | Layer | Where | What it stops |
|---|---|---|---|
| 1 | **Input guardrail** | `app/security/guardrails.py` | Pre-LLM regex check on the user prompt — rejects prompt-injection ("ignore previous instructions"), natural-language destructive intent ("drop the users table"), and obvious off-topic prompts before a single token is spent |
| 2 | **Agent system prompts** | every agent in `app/agents/*` | Each LLM is told it is read-only and on-topic; second-line filter for anything the regex missed |
| 3 | **SQL guard (AST)** | `app/security/sql_guard.py` | `sqlglot.parse()` walk blocks INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/CREATE/GRANT/REVOKE/CALL hidden inside CTEs/subqueries; stacked statements (`;\s*\S`) rejected; regex fallback for unparseable dialects |
| 4 | **Engine-level read-only** | `app/db/manager.py` | SQLite: `PRAGMA query_only = ON` attached as a connect listener (also applied retroactively to the pooled connection after CSV/Excel `df.to_sql`); Postgres: `connect_args.options = "-c default_transaction_read_only=on -c statement_timeout=30000"` — even if all higher layers were bypassed, the database refuses writes |
| 5 | **UI affordances** | `frontend/src/components/QueryBar.tsx` | Visible "READ-ONLY" badge + deterministic refusal copy on guardrail hits — users see the guarantee up front |

### Other controls

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
| LLM rate limits | Exponential back-off (3 retries, 5s initial); per-agent model split lets cheap roles use 8B and only Planner/Insight hit 70B | Queue + worker pool |
| Graph cache | In-process dict per (session × model × provider) | Eviction + TTL |
| Multi-agent fan-out | `asyncio.gather` over SQL Workers; one Python process | Worker pool with shared cache + async LLM HTTP client per agent |
| Per-query result cache | Redis (`cache_query_ttl`, default 5 min) | Already production-shaped; needs Redis HA |
| Secret key | File-backed dev fallback at `backend/data/.secret_key` | `APP_SECRET_KEY` env injected from secrets manager |
