# Low-Level Design — Semantic Reporting (NL-DB Query)

> Last updated: 2026-04-28 (rev. structured output across all agents, two-layer critic, orchestrator re-plan + clarification routing, DataFacts single-computation, example-queries endpoint, usage accumulator)

---

## Section 1: Backend Design

### 1.1 Module Map

```
backend/
├── app/
│   ├── main.py                       FastAPI app factory, CORS, lifespan (init_db + cache warmup)
│   ├── config.py                     Pydantic Settings (loaded from .env)
│   ├── api/
│   │   ├── routes.py                 NL/DB endpoints (connections, query/stream, exports)
│   │   ├── conversation_routes.py    Conversations, messages, preferences endpoints
│   │   └── schemas.py                Pydantic request / response models
│   ├── agents/
│   │   ├── orchestrator.py           Multi-agent pipeline driver — emits SSE events
│   │   ├── intent_classifier.py      Stage 1: classifies the prompt (structured output)
│   │   ├── schema_agent.py           Stage 2: cached DDL + per-column profiles
│   │   ├── planner.py                Stage 3: builds typed AnalysisPlan (structured output)
│   │   ├── sql_workers.py            Stage 4: parallel queries + two-strategy repair
│   │   ├── viz_designer.py           Stage 5: deterministic visual builder
│   │   ├── insight_agent.py          Stage 6: DataFacts-grounded narrative (structured output)
│   │   ├── critic.py                 Stage 7: two-layer quality gate (structured output)
│   │   ├── _usage.py                 Pipeline-wide token accumulator (contextvars)
│   │   ├── llm_factory.py            Per-agent LLM resolver (model/provider/temp/max_tokens)
│   │   └── sql_agent.py              Legacy LangGraph ReAct agent — used by /query (non-streaming)
│   ├── cache/
│   │   ├── __init__.py               Re-exports `cache` singleton
│   │   └── cache.py                  Two-tier cache (Redis → in-process TTLCache)
│   ├── db/
│   │   ├── manager.py                ConnectionManager — in-memory live engine registry
│   │   ├── app_db.py                 SQLAlchemy 2.0 engine + session_scope (app metadata)
│   │   ├── models.py                 ORM models: Connection, Conversation, Message, Preference
│   │   └── repositories.py           Repository classes wrapping all SQL access
│   ├── security/
│   │   ├── guardrails.py             Input-stage check (prompt-injection, off-topic, destructive intent)
│   │   ├── sql_guard.py              AST-level read-only SQL validation (sqlglot) — hardened
│   │   └── crypto.py                 Fernet encrypt/decrypt for credentials at rest
│   ├── services/
│   │   ├── viz_service.py            ECharts JSON builder + Plotly PNG renderer
│   │   ├── export_service.py         CSV / Excel / PDF export
│   │   └── conversation_service.py   get_or_create_conversation, append_*_message helpers
│   └── mcp/
│       └── server.py                 FastMCP ASGI sub-app at /mcp
├── data/                             App metadata DB + auto-generated dev secret key (gitignored)
└── requirements.txt                  pip-installable dependency list
```

---

### 1.2 `app/config.py` — Settings

```python
class Settings(BaseSettings):
    # ---- LLM ----------------------------------------------------------------
    groq_api_key: str = ""
    llm_provider: str = "groq"                        # "groq" | "ollama"
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama-3.3-70b-versatile"
    agent_max_tokens: int = 512
    synthesis_max_tokens: int = 2048                  # legacy / unused
    agent_max_iterations: int = 2

    # ---- HTTP ---------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000

    # ---- Filesystem ---------------------------------------------------------
    upload_dir: str = ".../uploads"                   # auto-resolved
    data_dir:   str = ".../data"                      # auto-resolved

    # ---- Application database ----------------------------------------------
    app_db_url:  str  = "sqlite:///<data_dir>/app.db" # or postgresql+psycopg2://...
    app_db_echo: bool = False

    # ---- Redis cache --------------------------------------------------------
    redis_url:        str  = "redis://localhost:6379/0"
    redis_enabled:    bool = True
    cache_schema_ttl: int  = 86_400                   # 1 day
    cache_query_ttl:  int  = 300                      # 5 minutes
    cache_ollama_ttl: int  = 60                       # 1 minute

    # ---- Crypto -------------------------------------------------------------
    app_secret_key: str = ""                          # set via secrets manager in prod

    # ---- Per-agent LLM config (multi-agent pipeline) ------------------------
    # Each role has model_*, provider_*, max_tokens_*, temp_*; missing/zero
    # slots fall through to the global defaults (default_model / llm_provider /
    # agent_max_tokens / 0.0). Resolved via Settings.agent_config(name).
    AGENT_NAMES = (
        "intent_classifier",  # cheap+fast (8B)         — < 500 ms; structured output
        "planner",            # reasoning (70B)         — produces AnalysisPlan; structured output
        "schema",             # 8B (rarely an LLM call) — DDL + profiling
        "sql_agent",          # 70B tool-capable        — repair strategies A + B
        "viz_designer",       # 8B                      — chart-type heuristics (deterministic)
        "insight_agent",      # 70B (temp=0.3)          — DataFacts-grounded narrative; structured output
        "critic",             # 8B                      — two-layer quality gate; structured output
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def agent_config(self, name: str) -> AgentLLMConfig:
        """(model, provider, max_tokens, temperature) for the given role."""
```

**`.env` keys (all optional unless marked):**
```
# LLM
GROQ_API_KEY=gsk_...                # required when LLM_PROVIDER=groq
LLM_PROVIDER=groq                   # groq | ollama
DEFAULT_MODEL=llama-3.3-70b-versatile
AGENT_MAX_TOKENS=2048
AGENT_MAX_ITERATIONS=1

# App database
APP_DB_URL=sqlite:///./data/app.db   # or postgresql+psycopg2://user:pass@host/db
APP_DB_ECHO=false

# Redis (auto-falls-back to in-process if unreachable)
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=true
CACHE_SCHEMA_TTL=86400
CACHE_QUERY_TTL=300
CACHE_OLLAMA_TTL=60

# Crypto
APP_SECRET_KEY=                      # 44-char Fernet.generate_key().decode() — REQUIRED in prod

# Per-agent LLM (omit any line to inherit the global default)
# Per-agent overrides — omit any line to inherit global defaults.
# Structured-output agents (intent, planner, insight, critic) use include_raw=True;
# token budget must accommodate the JSON schema + reasoning + output.
MODEL_INTENT_CLASSIFIER=llama-3.1-8b-instant   PROVIDER_INTENT_CLASSIFIER=groq   MAX_TOKENS_INTENT_CLASSIFIER=400   TEMP_INTENT_CLASSIFIER=0
MODEL_PLANNER=llama-3.3-70b-versatile          PROVIDER_PLANNER=groq             MAX_TOKENS_PLANNER=2048            TEMP_PLANNER=0
MODEL_SCHEMA=llama-3.1-8b-instant              PROVIDER_SCHEMA=groq              MAX_TOKENS_SCHEMA=1024             TEMP_SCHEMA=0
MODEL_SQL_AGENT=llama-3.3-70b-versatile        PROVIDER_SQL_AGENT=groq           MAX_TOKENS_SQL_AGENT=1024          TEMP_SQL_AGENT=0
MODEL_VIZ_DESIGNER=llama-3.1-8b-instant        PROVIDER_VIZ_DESIGNER=groq        MAX_TOKENS_VIZ_DESIGNER=800        TEMP_VIZ_DESIGNER=0
MODEL_INSIGHT_AGENT=llama-3.3-70b-versatile    PROVIDER_INSIGHT_AGENT=groq       MAX_TOKENS_INSIGHT_AGENT=1500      TEMP_INSIGHT_AGENT=0.3
MODEL_CRITIC=llama-3.1-8b-instant              PROVIDER_CRITIC=groq              MAX_TOKENS_CRITIC=500              TEMP_CRITIC=0
```

The data directory (`backend/data/`) is created on import — SQLAlchemy creates `app.db` there on first start, and `crypto.py` writes `.secret_key` there as a dev fallback when `APP_SECRET_KEY` is unset.

---

### 1.3 `app/db/manager.py` — ConnectionManager

**Class:** `ConnectionManager`

| Internal dict | Key | Value |
|---|---|---|
| `_connections` | session_id (str) | `SQLDatabase` (LangChain) |
| `_engines` | session_id (str) | `sqlalchemy.Engine` (CSV/Excel only) |
| `_metadata` | session_id (str) | `dict` with type, name, tables, schema_ddl |

**Public methods:**

| Method | Signature | Returns | Notes |
|---|---|---|---|
| `connect_sqlite` | `(db_path, session_id=None) → str` | session_id | Resolves to absolute path |
| `connect_postgres` | `(host, port, database, user, password, session_id=None) → str` | session_id | URI: `postgresql://user:pw@host:port/db` |
| `load_csv` | `(file_path, table_name=None, session_id=None) → str` | session_id | pandas → in-memory `sqlite://` |
| `load_excel` | `(file_path, session_id=None) → str` | session_id | Each sheet → separate table |
| `get_db` | `(session_id) → SQLDatabase \| None` | SQLDatabase | Used by agent + routes |
| `get_metadata` | `(session_id) → dict \| None` | metadata dict | type, name, tables, schema_ddl |
| `get_tables` | `(session_id) → list[str]` | table names | Delegates to SQLDatabase |
| `is_connected` | `(session_id) → bool` | bool | Presence check |
| `list_sessions` | `() → list[dict]` | all sessions | Each entry includes session_id |
| `disconnect` | `(session_id) → None` | — | Pops all three dicts |

**Metadata dict shapes:**

```python
# SQLite
{"type": "sqlite", "path": str, "name": str, "tables": list, "schema_ddl": str}

# PostgreSQL
{"type": "postgresql", "host": str, "port": int, "database": str, "name": str, "tables": list, "schema_ddl": str}

# CSV
{"type": "csv", "file": str, "name": str, "table": str, "rows": int, "columns": list, "tables": list, "schema_ddl": str}

# Excel
{"type": "excel", "file": str, "name": str, "sheets": list, "tables": list, "schema_ddl": str}
```

**Singleton:** `connection_manager = ConnectionManager()` — module-level, shared across all requests.

---

### 1.4 `app/security/sql_guard.py` — SQL Guard

**Function:** `validate_read_only(sql: str, dialect: Optional[str] = None) → None`

Raises `ValueError` if the SQL contains any write or schema-altering operation.

**Validation pipeline:**
1. **Multi-statement check** — `;\s*\S` pattern after stripping trailing `;`
2. **AST walk** — `sqlglot.parse()` → walk every node → block on: `Insert`, `Update`, `Delete`, `Drop`, `TruncateTable`, `Alter`, `AlterColumn`, `Create`, `Grant`, `Revoke`, `Transaction`, `Command`, `Merge`
3. **Keyword fallback** — if sqlglot raises a parse error, strip `--` comments and `/* */` block comments, then regex scan for: `INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|REPLACE|MERGE|CALL|EXEC|EXECUTE`

**Allowed:** `SELECT`, `UNION`, `EXPLAIN`, `SHOW`, `DESCRIBE`, `PRAGMA`, `WITH` (read CTEs)

---

### 1.4b `app/security/guardrails.py` — Input Guardrails

**Functions:**
* `check_prompt(question: str) → GuardrailResult` — sync; returns `{passed: bool, category: "ok" | "prompt_injection" | "destructive" | "off_topic", refusal_message: str | None}`.
* `attach_to_orchestrator(yield_fn)` — convenience adapter used by `orchestrator.run_orchestrator` so a refusal becomes a clean `error` SSE event with no LLM call.

**What gets blocked (regex catalogue):**

| Category | Examples |
|---|---|
| `prompt_injection` | "ignore previous instructions", "you are now …", "system prompt is:", role-play takeovers, jailbreak phrasings |
| `destructive` | "drop the table", "delete all rows", "wipe", "truncate", any natural-language ask to mutate data |
| `off_topic` | obvious creative-writing / world-knowledge / unrelated-code asks; bias is conservative — subtle off-topic is left to the agent system prompts |

A hit returns a deterministic, user-facing refusal string. Zero LLM tokens, zero DB hit. This is the **first** of the five read-only defence layers.

---

### 1.4c `app/db/manager.py` — Engine-level read-only

The fourth layer is the database engine itself:

```python
# SQLite — PRAGMA query_only is per-connection and persists for its lifetime.
def _attach_sqlite_readonly(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.close()
    # Sweep any pre-existing pooled connection (e.g. from CSV df.to_sql).
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA query_only = ON")

# Postgres — psycopg2 connect_args.options
_PG_READONLY_CONNECT_ARGS = {
    "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
}
```

`connect_sqlite` calls `_attach_sqlite_readonly(db._engine)` after `from_uri`; `connect_postgres` passes `engine_args={"connect_args": _PG_READONLY_CONNECT_ARGS, "pool_pre_ping": True}` to `SQLDatabase.from_uri`. CSV / Excel loaders call the same helper *after* `df.to_sql` finishes so the data load itself isn't blocked, but every subsequent connection from that engine is read-only.

---

### 1.5 `app/agents/sql_agent.py` — Legacy LangGraph ReAct Agent

**Used by `POST /query` (non-streaming) only.** The streaming endpoint
`POST /query/stream` routes through `app/agents/orchestrator.py` (see
§1.5b). Documented here for completeness — cURL / scripted clients still
get the simpler behaviour.

#### State Schema

```python
class SQLAgentState(TypedDict):
    messages:      Annotated[list, add_messages]  # full conversation history
    question:      str                             # original user question
    iteration:     int                             # loop counter
    chart_specs:   list  # accumulated {id, option, title, sql}
    table_results: list  # accumulated {id, columns, rows, sql, title}
    new_charts:    list  # charts from last tools_node call (SSE emit)
    new_tables:    list  # tables from last tools_node call (SSE emit)
    last_sql:      str   # most recent SQL (for export_ctx SSE event)
    last_columns:  list
    last_rows:     list
```

#### Graph Topology

```
START
  │
  ▼
agent_node  ──── should_continue() ────► tools_node
                      │                      │
                      │ END                  └──► agent_node (loop)
                      ▼
                     END
```

**`should_continue(state)`** returns `"tools"` when:
- Last message is an `AIMessage` with `tool_calls`
- `state["iteration"] <= MAX_ITERATIONS + 1`

Otherwise returns `END`.

**`agent_node(state)`**:
- Prepends `SystemMessage(AGENT_SYSTEM)` to message history
- Calls `llm_with_tools.ainvoke()` with exponential back-off on 429/529
- Hard-stops at `iteration >= MAX_ITERATIONS + 2`

**`tools_node(state)`**:
- Iterates `last_ai.tool_calls`
- For `generate_chart` → intercepts `\x00CHARTJSON\x00` sentinel → extracts ECharts option → appends to `chart_specs`
- For `execute_sql` → intercepts `\x00TABLEJSON\x00` sentinel → extracts structured rows/columns → appends to `table_results`
- Strips sentinel from `ToolMessage.content` before LLM sees it
- Updates `last_sql`, `last_columns`, `last_rows` for export context

#### Tools

**`execute_sql(sql: str, title: str = "") → str`**
- Calls `validate_read_only(sql)` → returns `"BLOCKED: ..."` on violation
- `conn.execute(sa_text(sql))` → fetches all rows
- Returns: `\x00TABLEJSON\x00{json}` + human-readable pipe table (50 rows max)
- On error: returns `"ERROR: ..."`

**`generate_chart(sql, chart_type, x_col, y_col, title, sort, limit, color_col) → str`**
- Validates `x_col` and `y_col` are present in results before proceeding
- Calls `build_echarts_option(spec, rows, columns)` → ECharts JSON
- Returns: `\x00CHARTJSON\x00{json}` with option + data preview

#### LLM Factory

```python
def _make_llm(model, provider, streaming=False, max_tokens=None):
    if provider == "ollama":
        return ChatOllama(model=model, base_url=settings.ollama_base_url, temperature=0)
    return ChatGroq(model=model, api_key=settings.groq_api_key,
                    temperature=0, max_tokens=resolved, streaming=streaming)
```

#### Graph Cache

```python
_graph_cache: dict[str, Any] = {}
# key = "{session_id}:{provider}:{model}"
```

- `evict_session_agents(session_id)` — removes all keys prefixed with `session_id:` (called on disconnect)
- `_get_graph(session_id, db, model, provider, schema_ddl)` — cache-or-build

#### Public API

```python
async def run_query(db, question, model=None, provider=None, session_id=None, schema_ddl=None) → dict
# Returns:
#   {
#     "answer":        str,
#     "steps":         list[dict],
#     "chart_specs":   list[dict],
#     "table_results": list[dict],
#     "usage": {
#         "input_tokens":  int,
#         "output_tokens": int,
#         "total_tokens":  int,
#         "latency_ms":    int,    # wall-clock around graph.ainvoke
#     },
#   }
#
# Tokens are accumulated by walking ``result["messages"]`` and summing
# AIMessage.usage_metadata. Latency is measured around graph.ainvoke().

async def stream_query(db, question, model=None, provider=None, session_id=None, schema_ddl=None) → AsyncGenerator[dict, None]
# Yields SSE event dicts: token | tool_start | tool_end | chart_spec | table_data | export_ctx | usage | done
# usage event includes latency_ms — wall-clock duration measured around astream_events().
```

#### Telemetry — Tokens + Latency

| Field | Source | Notes |
|---|---|---|
| `input_tokens`  | `AIMessage.usage_metadata.input_tokens`  summed across the run | From `on_chat_model_end` events in `stream_query`; from message walk in `run_query` |
| `output_tokens` | `AIMessage.usage_metadata.output_tokens` summed across the run | Same |
| `total_tokens`  | `input_tokens + output_tokens` | Pre-computed for the client |
| `latency_ms`    | `int((time.perf_counter() - started) * 1000)` | Wall-clock around the whole agent run, including tool execution |

Returned in `/query` response body and emitted as a `usage` SSE event right
before `done` from `/query/stream`. Persisted to `Message.usage_json` for
the assistant message so it survives reload.

#### SSE Event Shapes

```python
{"type": "conversation",
 "conversation_id":      str,
 "user_message_id":      str | None,
 "assistant_message_id": str,
 "title":                str | None}                 # emitted FIRST when a thread is bound

{"type": "token",      "content": str}
{"type": "tool_start", "tool": str, "input": str}
{"type": "tool_end",   "tool": str, "output": str}    # output truncated to 600 chars
{"type": "chart_spec", "id": str, "option": dict, "title": str, "sql": str}
{"type": "table_data", "id": str, "columns": list, "rows": list, "sql": str, "title": str}
{"type": "export_ctx", "sql": str, "session_id": str}
{"type": "usage",      "input_tokens": int, "output_tokens": int, "total_tokens": int, "latency_ms": int}
{"type": "done"}
{"type": "error",      "content": str}
```

---

### 1.5b `app/agents/orchestrator.py` — Multi-Agent Pipeline

The streaming endpoint runs through **`run_orchestrator(question, db, session_id, schema_ddl_hint=None)`** — an `AsyncGenerator[dict, None]` that yields SSE-shaped event dicts at each pipeline stage. Route handler wraps each in `data: {json}\n\n`.

#### Pipeline constants

```python
_REPLAN_FAILURE_THRESHOLD = 0.50   # re-plan when this fraction of queries fail
_MAX_INSIGHT_RETRIES      = 2      # max critic-driven insight regeneration attempts
_CLARIFICATION_THRESHOLD  = 0.35   # confidence below this triggers clarification
```

#### Full pipeline flow

```
question
   │
   ├─→ start_bucket()             reset per-pipeline token accumulator
   │
   ├─→ classify_intent ──────────► Intent  ─→ event: intent
   │
   ├─→ get_schema_context ────────► SchemaContext (cached in Redis)
   │     ↑ fetched BEFORE trivial branches so replies can use real table names
   │
   ├─→ Trivial branch (no SQL, no planner):
   │     intent == "greeting"        → _greeting_reply(schema) → token stream → done
   │     intent == "help"            → _help_reply(schema)     → token stream → done
   │     confidence < 0.35           → clarification prompt    → token stream → done
   │
   ├─→ plan_analysis ─────────────► AnalysisPlan ─→ event: plan
   │     (no queries → polite token + done)
   │
   ├─→ _execute_plan_queries()       ─→ event: query_start × N
   │     AsyncGenerator — yields (event, result) in completion order
   │     per query: SQLGuard → execute → Strategy A (column fix)
   │                → Strategy B (full rewrite) → cache hit
   │                                               ─→ event: query_done × N
   │
   ├─→ Re-plan if >50% queries failed:
   │     amend question with error context → plan_analysis → re-execute
   │     adopt only if new_failed < original_failed
   │                                               ─→ event: plan (replan=true)
   │                                               ─→ event: query_start × N
   │                                               ─→ event: query_done × N
   │
   ├─→ design_visual × M ─────────► RenderedVisual ─→ event: viz × M
   │     also back-compat:                          ─→ event: chart_spec × M
   │                                               ─→ event: table_data × M
   │     after all visuals:                        ─→ event: dashboard_layout
   │
   ├─→ facts = compute_data_facts(plan, results)   ← computed ONCE, shared below
   │
   ├─→ generate_insights(…, data_facts=facts) ─────► InsightReport
   │     executive_summary streamed as token events ─→ event: insight
   │
   ├─→ Critic feedback loop (up to _MAX_INSIGHT_RETRIES):
   │     critique(…, data_facts=facts)
   │       Layer 1 programmatic: number hallucination + empty-result check
   │       Layer 2 LLM semantic: receives Layer 1 verdicts, semantic-only scope
   │     if error issues → generate_insights(…, critique_feedback=errors)
   │                                               ─→ event: insight (updated)
   │     final report:                            ─→ event: critique
   │
   ├─→ export_ctx (last successful SQL)           ─→ event: export_ctx
   │
   ├─→ totals() from usage accumulator            ─→ event: usage
   │     {intent_latency_ms, plan_latency_ms, insight_latency_ms,
   │      total_elapsed_ms, input_tokens, output_tokens, total_tokens}
   │
   └─→ event: done {elapsed_ms}
```

#### `_execute_plan_queries` — async generator

```python
async def _execute_plan_queries(
    plan: AnalysisPlan,
    db: SQLDatabase,
    session_id: str,
    schema_ddl: str,
) -> AsyncGenerator[tuple[dict[str, Any], QueryResult], None]:
    """
    Run all PlannedQuery tasks concurrently. Yields (query_done_event, result)
    tuples in completion order so the orchestrator streams them in real time.
    """
    pending: dict[str, asyncio.Task[QueryResult]] = {
        q.id: asyncio.create_task(run_one_query(q, db, session_id, schema_ddl))
        for q in plan.queries
    }
    while pending:
        done, _ = await asyncio.wait(pending.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            event = _evt("query_done", query_id=result.query_id, success=result.success,
                         rows_count=result.rows_count, latency_ms=result.latency_ms,
                         repaired=result.repaired, repair_strategy=result.repair_strategy,
                         error=result.error)
            yield event, result
            # remove finished task from pending
```

This generator is called twice when re-planning occurs — the same helper handles both the initial run and the re-plan run.

#### Dynamic trivial-intent reply helpers

```python
def _greeting_reply(schema: Optional[SchemaContext] = None) -> str:
    # Uses schema.profiles to list the first 4 real table names in the reply
    # e.g. "I have access to tables like *users*, *transactions*, *orders* and more."

def _help_reply(schema: Optional[SchemaContext] = None) -> str:
    # Appends schema.summary (e.g. "PostgreSQL: 12 tables") to the capability list
```

**Per-agent files (under `app/agents/`):**

| File | Role | LLM | Structured Output | Notes |
|---|---|---|---|---|
| `intent_classifier.py` | Stage 1 | 8B (400 tok, temp 0) | `Intent` via `with_structured_output` | Deterministic short-circuit for greetings/help (no LLM); `latency_ms` set post-call |
| `schema_agent.py` | Stage 2 | 8B (rarely used) | `SchemaContext` (direct Python) | Cached in Redis at `cache_schema_ttl` (1 day); fetched before trivial branches |
| `planner.py` | Stage 3 | 70B (2048 tok, temp 0) | `AnalysisPlan` via `with_structured_output` | `_validate_plan()` fixes referential integrity post-parse; `_safe_*` coerce fallback; `_validate_plan()` builds default layout if LLM omits it |
| `sql_workers.py` | Stage 4 | 70B (repair only, 1024 tok) | `QueryResult` (plain Python) | `validate_read_only()` before first exec + before each repair; Strategy A (column fix) → Strategy B (full rewrite) on failure; results cached for `cache_query_ttl` (5 min) |
| `viz_designer.py` | Stage 5 | none — deterministic | `RenderedVisual` (plain Python) | No LLM call; maps `PlannedVisual + QueryResult` → KPI / ECharts option / table |
| `insight_agent.py` | Stage 6 | 70B (1500 tok, temp 0.3) | `InsightReport` via `with_structured_output` | Never sees raw rows — only `DataFacts`; `latency_ms` has `exclude=True` (not in LLM schema); max 6 key_findings, max 3 anomalies/recommendations |
| `critic.py` | Stage 7 | 8B (500 tok, temp 0) | `_CriticLLMOutput` via `with_structured_output` | Layer 1 programmatic runs before LLM; LLM receives verified-numbers block; result merged into `CritiqueReport`; never blocks delivery |
| `_usage.py` | infra | — | — | `contextvars.ContextVar`-based; `start_bucket()` / `record(response)` / `totals()` |
| `llm_factory.py` | infra | — | — | `llm_for(name, *, streaming, override_model, override_provider, override_max_tokens)` — single source of truth for `ChatGroq` / `ChatOllama` instantiation |

**`AGENT_NAMES`** (in `app/config.py`) is the canonical list — typos raise `KeyError` from `Settings.agent_config(name)` so misconfiguration surfaces at boot, not at runtime.

#### Structured output pattern (used by all LLM agents)

```python
llm = llm_for("agent_name")
structured_llm = llm.with_structured_output(OutputModel, include_raw=True)
result = await structured_llm.ainvoke([SystemMessage(content=...), HumanMessage(content=...)])
from app.agents._usage import record as _record_usage
_record_usage(result["raw"])                       # token tracking
if result["parsed"] is not None and result["parsing_error"] is None:
    return result["parsed"].model_copy(update={"latency_ms": elapsed()})
# fallback: lenient JSON parse on result["raw"].content
```

#### `_CriticLLMOutput` — internal critic model

```python
class _CriticLLMOutput(BaseModel):
    passed: bool   # True only if NO error-severity issues
    score: float   # 1.0 - (0.3 × errors) - (0.1 × warnings) - (0.02 × info)
    issues: list[Issue]  # semantic only; must NOT include ✓-verified numbers

# critique() merges _CriticLLMOutput with programmatic Layer 1 issues:
final_score = max(0.0, min(1.0, llm_score - len(prog_issues) * 0.2))
return CritiqueReport(passed=not has_error and llm_passed, score=final_score, issues=all_issues)
```

#### `DataFacts` — anti-hallucination grounding

```python
# In orchestrator (called ONCE):
facts: list[QueryFacts] = compute_data_facts(plan, results)

# Passed to both:
insight = await generate_insights(question, intent, plan, results, data_facts=facts)
report  = await critique(question, intent, plan, results, insight, data_facts=facts)
```

`QueryFacts` contains per-column `ColumnFacts` with `col_type`, `row_count`, `min_val`, `max_val`, `sum_val`, `avg_val`, `all_unique_values` (capped at 50), `top_values` (top 15 categories). The Insight Agent never sees raw rows.

#### SSE event shapes (streaming endpoint)

```
{type: "conversation",   conversation_id, user_message_id, assistant_message_id, title}
{type: "intent",         intent, wants_chart, wants_dashboard, wants_export, chart_hints,
                         time_window, complexity, keywords, confidence, latency_ms}
{type: "plan",           title, description, query_count, visual_count, layout,
                         latency_ms, replan: bool}   # replan=true on the re-plan event
{type: "query_start",    query_id, purpose}
{type: "query_done",     query_id, success, rows_count, latency_ms,
                         repaired: bool, repair_strategy: "column_fix"|"full_rewrite"|null,
                         error: str|null}
{type: "viz",            visual_id, visual_type, title, subtitle, from_query, kpi,
                         echarts_option, table_columns, table_rows, rows_count, error}
{type: "chart_spec",     id, option, title, sql}                  # back-compat
{type: "table_data",     id, columns, rows, sql, title}            # back-compat
{type: "dashboard_layout", title, layout, visuals}
{type: "insight",        headline, executive_summary, key_findings, anomalies,
                         recommendations}   # latency_ms excluded from LLM output
{type: "token",          content}                                  # streamed exec summary
{type: "critique",       passed, score, issues[{severity, category, message, location}],
                         latency_ms}
{type: "export_ctx",     sql, session_id}                          # last successful SQL
{type: "usage",          intent_latency_ms, plan_latency_ms, insight_latency_ms,
                         total_elapsed_ms, input_tokens, output_tokens, total_tokens,
                         latency_ms}
{type: "done",           elapsed_ms}
{type: "error",          content}
```

Older clients that only handle `token` / `chart_spec` / `table_data` / `usage` / `done` continue to work — the orchestrator emits both new and back-compat shapes.

---

### 1.5c `app/agents/_usage.py` — Pipeline Token Accumulator

```python
# Lifecycle (per pipeline request):
start_bucket()          # orchestrator calls this at run start; resets ContextVar
record(response)        # each agent calls after LLM call; accepts AIMessage or dict
totals() -> (int, int)  # orchestrator calls at end; returns (input_tokens, output_tokens)
```

Uses `contextvars.ContextVar[_UsageBucket]` — Python's `asyncio` automatically propagates `ContextVar` state into spawned `asyncio.Task` objects, so concurrent SQL workers all write to the same per-request bucket without any explicit synchronisation.

`record()` accepts either a LangChain `AIMessage` (reads `.usage_metadata`) or a plain `dict`. Silently no-ops when called outside an active pipeline (e.g. ad-hoc scripts or tests).

---

### 1.5d `app/agents/sql_workers.py` — Two-Strategy Repair

`run_one_query` flow:

```
cache hit → return cached result

validate_read_only(pq.sql)  ← block write intent immediately (no repair)

execute pq.sql
  success → cache + return

  fail → Strategy A: _strategy_column_fix(bad_sql, error, purpose, schema_ddl)
            LLM is shown the failing SQL + error and asked to fix column/table names only
            validate_read_only(repaired_a)
            execute repaired_a
              success → cache + return (repaired=True, repair_strategy="column_fix")

  fail → Strategy B: _strategy_full_rewrite(purpose, schema_ddl, all_errors)
            LLM is given only purpose + schema (not the broken SQL) and writes fresh SQL
            validate_read_only(repaired_b)
            execute repaired_b
              success → cache + return (repaired=True, repair_strategy="full_rewrite")

  fail → return QueryResult(success=False, error=last_two_errors joined)
```

Both strategies use `llm_for("sql_agent")` and call `_record_usage(resp)` for token tracking.

---

### 1.6 `app/services/viz_service.py` — Visualization Service

#### ChartSpec dataclass

```python
@dataclass
class ChartSpec:
    chart_type: str = "bar"
    title: str = ""
    x: str = ""
    y: str = ""
    color: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    aggregation: str = ""    # sum | count | avg | max | min
    sort: str = ""           # asc | desc
    limit: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
```

#### ECharts Builder — `build_echarts_option(spec, rows, columns) → dict`

Supported chart types and their ECharts `series.type`:

| `chart_type` | ECharts type | Notes |
|---|---|---|
| `bar` | `bar` | Vertical gradient, label on top ≤10 items |
| `horizontal_bar` | `bar` (orientation=h) | Horizontal gradient |
| `line` | `line` | Smooth, markers |
| `area` | `line` | + `areaStyle` with gradient fill |
| `pie` | `pie` | `radius: ["0%", "68%"]` |
| `donut` | `pie` | `radius: ["45%", "72%"]` |
| `scatter` | `scatter` | x/y both numeric |
| `funnel` | `funnel` | Sorted descending |
| `treemap` | `treemap` | No breadcrumb |
| `gauge` | `gauge` | Single value |
| `histogram` | `bar` | 10-bin auto-binning |
| `box` | `boxplot` | Structure only (data empty) |

All charts share a dark theme: `backgroundColor: "#0a0f1e"`, `textStyle.color: "#e2e8f0"`, grid color `#1e2a45`.

#### Plotly PNG Renderer — `render_chart(spec, rows, columns, width, height) → str`

- Builds pandas DataFrame from rows/columns
- Applies aggregation + sort + limit via `_apply_agg()`
- Calls `_make_figure()` → Plotly figure
- `fig.to_image(format="png", scale=2)` via Kaleido
- Returns base64-encoded PNG string

Supported Plotly types: `bar`, `horizontal_bar`, `line`, `area`, `scatter`, `pie`, `donut`, `histogram`, `heatmap`, `treemap`, `funnel`, `box`, `violin`, `bubble`, `waterfall`, `gauge`, `indicator`

---

### 1.6b `app/db/app_db.py` — Application Metadata DB

SQLAlchemy 2.0 sync engine for application state (NOT user data).

```python
engine: Engine = create_engine(
    settings.app_db_url,
    future=True,
    pool_pre_ping=True,
    echo=settings.app_db_echo,
    connect_args={"check_same_thread": False} if SQLITE else {},
)

# WAL mode + sane pragmas wired via SQLAlchemy "connect" event:
#   journal_mode=WAL, synchronous=NORMAL, foreign_keys=ON,
#   busy_timeout=5000, temp_store=MEMORY

class Base(DeclarativeBase): ...

@contextmanager
def session_scope() -> Iterator[Session]:
    """commit on clean exit, rollback on exception, close always."""

def init_db() -> None:
    """Idempotent — Base.metadata.create_all(); called from FastAPI lifespan."""
```

#### ORM Models (`app/db/models.py`)

```python
class Connection(Base):                                # __tablename__ = "connections"
    id, type, name,
    path,                                              # SQLite
    host, port, database, username, password_enc,     # Postgres (password Fernet-encrypted)
    upload_path,                                       # CSV/Excel
    meta_json: dict, is_active: bool,
    created_at, last_used_at

class Conversation(Base):                              # __tablename__ = "conversations"
    id, title, connection_id (FK→Connection),
    model, provider,
    created_at, updated_at,
    messages: relationship[Message]                    # cascade="all, delete-orphan"

class Message(Base):                                   # __tablename__ = "messages"
    id, conversation_id (FK), role,                    # role = "user" | "assistant"
    content,
    charts_json, tables_json, steps_json,              # JSON columns for UI hydration
    usage_json,                                        # {input_tokens, output_tokens, total_tokens, latency_ms}
    export_sql, status, error,
    created_at

class Preference(Base):                                # __tablename__ = "preferences"
    id (singleton = 1),
    model, provider,
    active_connection_id, active_conversation_id,
    updated_at
```

Indexes: `idx_conversation_updated(updated_at)`, `idx_message_conversation(conversation_id, created_at)`.

#### Repositories (`app/db/repositories.py`)

| Repo | Methods |
|---|---|
| `ConnectionRepo`  | `upsert`, `get`, `get_password` (decrypts), `list`, `soft_delete`, `hard_delete`, `touch` |
| `ConversationRepo`| `create`, `get`, `list` (with message count), `update`, `touch`, `delete` |
| `MessageRepo`     | `add`, `list` (ordered by `created_at`) |
| `PreferenceRepo`  | `get` (auto-creates singleton row), `update`, `to_dict` |

Wire-format helpers: `connection_to_dict`, `conversation_to_dict`, `message_to_dict` — never expose `password_enc`.

---

### 1.6c `app/services/conversation_service.py` — Conversation Helpers

Higher-level helpers that own their own `session_scope`. Used by `/query/stream` to persist user prompts and assistant replies.

```python
def get_or_create_conversation(*, conversation_id, question, connection_id, model, provider) -> dict | None
# Resolves an existing conversation or creates one titled from the question
# (truncated to 60 chars). Returns None when persistence is disabled.

def append_user_message(*, conversation_id, question, message_id=None) -> dict
# Inserts a "user" Message row + bumps Conversation.updated_at.

def append_assistant_message(
    *, conversation_id, answer, charts, tables, steps, usage, export_sql,
    status="done", error=None, message_id=None
) -> dict
# Inserts the assistant Message with all its rendering payloads + usage telemetry.

def new_message_id() -> str   # uuid4
```

---

### 1.6d `app/cache/cache.py` — Two-Tier Cache

```python
cache = _Cache()                                      # module-level singleton

# Sync API
cache.get(key) -> Any | None
cache.set(key, value, ttl: int | None)
cache.delete(key)
cache.delete_prefix(prefix)                           # SCAN-based; safe on large keysets

# Async wrappers (run on default executor)
await cache.aget(key)
await cache.aset(key, value, ttl)

cache.healthy: bool                                   # True when Redis reachable
cache.shutdown()                                      # called from lifespan exit

# Helpers
query_cache_key(connection_id, sql) -> str            # "query:<conn_id>:<sha256(sql)>"
```

Behaviour:
- Tries Redis (with 0.5s connect / 1.0s op timeouts). On `RedisError`/`OSError`, flips `_healthy = False` and serves from `cachetools.TTLCache(maxsize=4096, ttl=600)`.
- `_maybe_recover()` re-pings Redis at most every 30 seconds.
- All values are JSON-serialised (`default=str`) — non-JSON values are skipped with a warning.
- Key prefix `dl:` is added by the cache layer; callers pass clean keys.
- TTL semantics: when Redis is healthy, per-key TTL via `SET ... EX`. On the local fallback, the global 600s TTL applies (cachetools doesn't support per-key TTL).

Currently used by `/api/v1/ollama/models` (60s TTL); `cache_query_ttl` and `cache_schema_ttl` are reserved for future query-result and schema caching.

---

### 1.6e `app/security/crypto.py` — Encryption at Rest

Fernet (AES-128-CBC + HMAC-SHA256) symmetric encryption.

```python
encrypt(plaintext: str) -> str          # urlsafe-base64 ciphertext
decrypt(ciphertext: str) -> str         # raises ValueError on tampering / wrong key
```

Key resolution order:
1. `settings.app_secret_key` — production. Must be a 32-byte urlsafe base64 string (i.e. `Fernet.generate_key().decode()`).
2. `backend/data/.secret_key` — auto-generated for dev on first run. Persisted with `chmod 0600` where supported. **Gitignore — never commit.**

Used by `ConnectionRepo.upsert` to write `password_enc` and `ConnectionRepo.get_password` to recover plaintext when re-opening a Postgres engine.

---

### 1.7 API Routes — Complete Endpoint Reference

All routes mounted under `/api/v1`.

| Method | Path | Request Body | Response `data` | Notes |
|---|---|---|---|---|
| GET | `/config` | — | `{default_model, llm_provider, ollama_base_url}` | Public config |
| GET | `/ollama/models` | — | `[{id, label}]` | Queries Ollama `/api/tags` |
| POST | `/connections/sqlite` | `SQLiteConnectRequest` | `{session_id, type, name, path, tables}` | 404 if file missing |
| POST | `/connections/postgres` | `PostgresConnectRequest` | `{session_id, type, name, host, port, database, tables}` | 400 on auth fail |
| POST | `/connections/upload` | `multipart/form-data file=` | `{session_id, type, name, tables, ...}` | CSV/XLSX/XLS only |
| GET | `/connections` | — | `[{session_id, ...}]` | All active sessions |
| GET | `/connections/{session_id}` | — | `{session_id, ...}` | 404 if not found |
| DELETE | `/connections/{session_id}` | — | `{message}` | Evicts agent cache too |
| GET | `/connections/{session_id}/tables` | — | `[table_name, ...]` | 404 if not found |
| GET | `/connections/{session_id}/example-queries` | — | `["question 1", …]` (4 items) | LLM-generated schema-aware questions; cached at `cache_schema_ttl`; fallback to 4 generic questions on LLM failure |
| POST | `/query` | `QueryRequest` | `{session_id, answer, steps, usage}` | Blocking; `usage` carries tokens + latency |
| POST | `/query/stream` | `QueryRequest` | `text/event-stream` | SSE; persists user + assistant messages when `conversation_id` is bound |
| POST | `/visualize` | `ChartRequest` | `{chart_b64, columns, row_count}` | Plotly PNG |
| POST | `/export/csv` | `ExportRequest` | `text/csv` binary | Attachment |
| POST | `/export/excel` | `ExportRequest` | `.xlsx` binary | Optional chart embed |
| POST | `/export/pdf` | `ExportRequest` | `.pdf` binary | Optional chart embed |

**Conversation & preference endpoints (`app/api/conversation_routes.py`):**

| Method | Path | Request Body | Response `data` | Notes |
|---|---|---|---|---|
| GET    | `/conversations`                             | —                       | `[{id, title, connection_id, model, provider, created_at, updated_at, message_count}]` | Sorted by `updated_at` desc |
| POST   | `/conversations`                             | `ConversationCreate`    | conversation dict                                                                       | 201 — auto-titled if `title` omitted |
| GET    | `/conversations/{conversation_id}`           | —                       | `{conversation, messages: [PersistedMessage…]}`                                         | 404 if not found |
| PATCH  | `/conversations/{conversation_id}`           | `ConversationUpdate`    | conversation dict                                                                       | Rename / rebind connection / change model |
| DELETE | `/conversations/{conversation_id}`           | —                       | `{deleted: <id>}`                                                                       | Cascades to messages |
| GET    | `/conversations/{conversation_id}/messages`  | —                       | `[PersistedMessage…]`                                                                   | Ordered by `created_at` |
| GET    | `/preferences`                               | —                       | `{model, provider, active_connection_id, active_conversation_id, updated_at}`           | Singleton row (`id=1`) |
| PATCH  | `/preferences`                               | `PreferenceUpdate`      | preference dict                                                                         | Partial update |

`PersistedMessage` shape: `{id, conversation_id, role, content, charts, tables, steps, usage, export_sql, status, error, created_at}`. The `usage` field is the `{input_tokens, output_tokens, total_tokens, latency_ms}` envelope.

**Health endpoint (root):**

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `{"status": "ok", "service": "datalens-ai", "cache_backend": "redis" \| "in-memory"}` |

**MCP endpoint:**

| Path | Notes |
|---|---|
| `/mcp` | FastMCP ASGI sub-app, HTTP transport |

---

### 1.8 Pydantic Schemas

```python
# Request
class SQLiteConnectRequest:  db_path: str;  session_id: Optional[str]
class PostgresConnectRequest: host, port(5432), database, user, password, session_id=None
class QueryRequest:  session_id, question(min_len=1), model=None, provider=None,
                     conversation_id: Optional[str] = None
class ExportRequest: session_id, sql(min_len=3), chart_b64=None, title="Data Report"
class ChartRequest:  session_id, sql, chart_spec: dict, width=900, height=500

class ConversationCreate: title=None, connection_id=None, model=None, provider=None
class ConversationUpdate: title=None, connection_id=None, model=None, provider=None
class PreferenceUpdate:   model=None, provider=None, active_connection_id=None, active_conversation_id=None

# Response fragments
class QueryStep:  type: Literal["tool_call","tool_result","ai_message"], tool, input, output, content

# Envelope (all endpoints)
class ApiResponse:  data: Any;  error: Optional[str]
```

---

## Section 2: Frontend Design

### 2.1 File Structure

```
frontend/src/
├── main.tsx               React DOM root, Tailwind import
├── App.tsx                Root layout; runs hydration + preference-sync hooks; ⌘B sidebar toggle
├── index.css              Tailwind directives + custom scrollbar
├── commands.ts            Slash-command registry consumed by CommandPalette
├── api/
│   └── client.ts          Typed fetch wrappers + multi-agent streamQuery callbacks
├── store/
│   └── index.ts           Zustand + persist middleware (localStorage); sidebarCollapsed slice
├── types/
│   └── index.ts           TS types for legacy + multi-agent payloads
├── hooks/
│   ├── useAnalysis.ts     SSE stream hook — wires the multi-agent pipeline events
│   └── useHydrate.ts      useHydrate / useConversationSync / usePreferenceSync
└── components/
    ├── Header.tsx           Top bar: branding, segmented Cloud/Local, model picker, status dot
    ├── Sidebar.tsx          ChatGPT-style nav: New chat / Connect database, conversations
    │                        bucketed by date, type-grouped connections, search, collapsible
    ├── CommandPalette.tsx   ⌘K palette — fuzzy-matched slash commands
    ├── ConnectionPanel.tsx  DB connect / upload form (SQLite / Postgres / CSV / Excel)
    ├── QueryBar.tsx         Natural-language input + READ-ONLY badge + refusal copy
    ├── AnalysisCard.tsx     Per-question card; routes between chat-style and dashboard
    ├── DashboardCanvas.tsx  12-column CSS grid renderer for {layout, visuals} from the Planner
    ├── KPICard.tsx          Big-number tile (label, value, optional unit / delta / sparkline)
    ├── InsightSection.tsx   Renders InsightReport (headline, exec summary, findings) + Critic
    ├── EChartCard.tsx       Interactive ECharts wrapper
    ├── DataTable.tsx        Tabular query result display
    ├── AgentProgress.tsx    Live per-stage progress (intent → plan → queries → viz → insight)
    └── InsightPanel.tsx     Legacy streaming-text panel; renders ↑in ↓out tok · latency
```

### 2.2 Zustand Store (`store/index.ts`)

The store splits cleanly into a **persisted slice** (saved to `localStorage`
via `zustand/middleware.persist`) and an **ephemeral slice** (rehydrated
from the backend on mount).

```typescript
interface AppStore {
  // ---- Persisted slice (localStorage key "datalens-ai-state", v1) -------
  model: string                                   // default "llama-3.3-70b-versatile"
  provider: LlmProvider                            // default "groq"
  activeSessionId:      string | null
  activeConversationId: string | null

  // ---- Ephemeral slice -------------------------------------------------
  hydrated: boolean
  sessions:        Session[]
  conversations:   Conversation[]
  analyses:        AnalysisResult[]                // current conversation thread
  isQuerying:      boolean
  ollamaModels:    ModelOption[]
  activeAnalysisId: string | null

  // ---- Pref setters ----------------------------------------------------
  setModel(m: string), setProvider(p: LlmProvider),
  setActiveSession(id: string | null),
  setActiveConversation(id: string | null)

  // ---- Hydration -------------------------------------------------------
  setHydrated(h: boolean),
  setSessions(s: Session[]), setConversations(c: Conversation[]),
  upsertConversation(c: Conversation), removeConversation(id: string)

  // ---- Sessions --------------------------------------------------------
  addSession(s: Session), removeSession(id: string)

  // ---- Analyses --------------------------------------------------------
  setAnalyses(a: AnalysisResult[]),
  loadAnalysesFromMessages(messages: PersistedMessage[]),     // hydrate from DB
  startAnalysis(id, question),
  appendToken(id, token),
  addChart(id, chart), addTable(id, table), addStep(id, step),
  setExportCtx(id, sql, sessionId),
  setUsage(id, usage),                                         // {…, latency_ms}
  finalizeAnalysis(id), setAnalysisError(id, error),
  attachServerIds(id, conversationId, messageId?),             // bind streamed → persisted
  setActiveAnalysis(id: string | null)

  setOllamaModels(models: ModelOption[]), reset()
}
```

`partialize` restricts the localStorage payload to `{model, provider, activeSessionId, activeConversationId}` — large lists (sessions, conversations, analyses) are always re-fetched from the server. The internal `messagesToAnalyses(messages)` helper adapts a list of `PersistedMessage` rows back into the `AnalysisResult` shape so a hydrated card renders identically to a freshly-streamed one.

### 2.3 `useAnalysis.ts` — SSE Stream Hook

Drives one streamed query run end-to-end. Handles **both** event tracks: the new multi-agent pipeline payloads *and* the legacy chat-style ones, so the UI smoothly degrades when a conversation jumps between simple Q&A and full dashboard intents.

```typescript
async function runQuery(question: string): Promise<void>
```

Flow:
1. `POST /api/v1/query/stream` with `session_id + question + model + provider + conversation_id`
2. Read `response.body` as `ReadableStream`, split on `\n`, parse `data: {...}` lines
3. Dispatch to the store based on `event.type`:
   - `conversation`       → `attachServerIds(cardId, conversation_id, assistant_message_id)`; `upsertConversation` if new
   - **`intent`**         → `attachIntent(cardId, IntentInfo)` — drives the AgentProgress timeline
   - **`plan`**           → `attachPlan(cardId, PlanInfo)` — DashboardCanvas reads `layout`
   - **`query_start`**    → push `QueryProgress {status: 'running'}`
   - **`query_done`**     → patch `QueryProgress` with rows / latency / repaired / error
   - **`viz`**            → `addRenderedVisual(cardId, RenderedVisual)`
   - **`dashboard_layout`** → finalise the layout payload for `DashboardCanvas`
   - **`insight`**        → `attachInsight(cardId, InsightReport)`
   - **`critique`**       → `attachCritique(cardId, CritiqueReport)`
   - `chart_spec`         → `addChart(cardId, {id, option, title, sql})`     # back-compat
   - `table_data`         → `addTable(cardId, {id, columns, rows, sql, title})` # back-compat
   - `token`              → `appendToken(cardId, content)`
   - `tool_start`/`tool_end` → `addStep(cardId, …)`                          # legacy ReAct
   - `export_ctx`         → `setExportCtx(cardId, sql, session_id)`
   - `usage`              → `setUsage(cardId, PipelineUsage)` — incl. per-agent latency_ms
   - `error`              → `setAnalysisError(cardId, content)`
   - `done`               → `finalizeAnalysis(cardId)`

The hook also calls `setCurrentAbort(controller.abort)` so the **`/stop`** slash command can cancel the in-flight stream.

### 2.3b `useHydrate.ts` — Bootstrap & Sync Hooks

Three companion hooks invoked by `App.tsx`:

| Hook | Trigger | Behaviour |
|---|---|---|
| `useHydrate()`         | Mount (once)                                       | Sequentially calls `getPreferences` → `listConnections` → `listConversations` → `getConversation(active)` and seeds the store. Failures swallowed individually so a missing piece never blocks boot. Final `setHydrated(true)` unlocks the sync hooks. |
| `useConversationSync()`| `activeConversationId` change after hydration       | Re-fetches `getConversation(id)` and replaces `analyses` via `loadAnalysesFromMessages`. Falls back to empty list on error. |
| `usePreferenceSync()`  | Any of `model / provider / activeSessionId / activeConversationId` change after hydration | 250 ms-debounced `PATCH /preferences` so the next device or refresh inherits the latest selection. Errors are silently ignored. |

### 2.4 `api/client.ts` — HTTP Client

Typed wrappers around `fetch` exposed as `api.*`:

```typescript
// Config + bootstrap
api.getConfig(): Promise<{ default_model, llm_provider, ollama_base_url? }>
api.getOllamaModels(): Promise<ModelOption[]>
api.listConnections(): Promise<(Session & { session_id: string })[]>

// Connections
api.connectSQLite(db_path): Promise<Session>
api.connectPostgres({host, port, database, user, password}): Promise<Session>
api.uploadFile(file: File): Promise<Session>
api.disconnect(session_id): Promise<void>

// Conversations + preferences (persisted)
api.listConversations(): Promise<Conversation[]>
api.createConversation({title?, connection_id?, model?, provider?}): Promise<Conversation>
api.getConversation(id): Promise<{ conversation, messages: PersistedMessage[] }>
api.renameConversation(id, title): Promise<Conversation>
api.updateConversation(id, patch): Promise<Conversation>
api.deleteConversation(id): Promise<{ deleted: string }>
api.getPreferences(): Promise<UserPreferences>
api.updatePreferences(patch): Promise<UserPreferences>

// Streaming + exports
api.streamQuery(sessionId, question, model, provider, callbacks, conversationId?): () => void
//   callbacks: {
//     onConversation, onToken, onChart, onTable, onStep, onExportCtx,
//     onUsage, onDone, onError,
//     // Multi-agent pipeline (all optional for back-compat):
//     onIntent, onPlan, onQueryStart, onQueryDone, onViz, onLayout,
//     onInsight, onCritique
//   }
api.exportCsv  (sessionId, sql, title): Promise<void>     // browser download
api.exportExcel(sessionId, sql, title): Promise<void>
api.exportPdf  (sessionId, sql, title): Promise<void>
```

`streamQuery` callbacks include `onUsage(u)` where `u = { input_tokens, output_tokens, total_tokens, latency_ms }`.

All JSON responses are unwrapped from the `{data, error}` envelope. Errors throw with FastAPI `detail` (string or validation array flattened to `field: message; …`).

### 2.5 Component Responsibilities

| Component | Props / State | Output |
|---|---|---|
| `ConnectionPanel` | local form state | Calls `connectSQLite` / `connectPostgres` / `uploadFile`, sets active session |
| `QueryBar` | `question: string`, `isStreaming: bool` | Submits to `useAnalysis.runQuery()` |
| `AnalysisCard` | `card: AnalysisCard` | Markdown answer + `AgentProgress` + `EChartCard[]` + `DataTable[]` + export buttons |
| `EChartCard` | `option: EChartsOption`, `title: string` | `<ReactECharts>` wrapper, dark theme, responsive |
| `DataTable` | `columns: string[]`, `rows: any[][]`, `title: string` | Styled HTML table, max-height scroll |
| `AgentProgress` | `steps: QueryStep[]` | Tool call timeline with icons |
| `Sidebar` | reads `sessions[]`, `conversations[]`, `activeSessionId`, `activeConversationId`, `sidebarCollapsed` from the store | ChatGPT-style nav: top-pinned **New chat** button + collapsible **Connect database** panel; conversations bucketed by date (`groupByDate`) — collapsed view shows a flat *Recent 5* list, *Show all* expands into Today / Yesterday / Previous 7 days / Previous 30 days / Older; each row supports inline rename (Enter / Escape) and confirm-then-delete; connections in a separate type-grouped section (SQLite / Postgres / CSV / Excel buckets driven by `TYPE_META`) with per-source-type accent colours and a search input |
| `Header` | reads `model`, `provider`, `activeSessionId`, `sessions` | Branding with DB-connected status dot + v1.1 pill; segmented Cloud/Local provider control replacing the old dropdown; model selector with status beacon; subtle gradient bottom accent |
| `CommandPalette` | global ⌘K | Fuzzy-matched slash commands (`/help`, `/clear`, `/new`, `/disconnect`, `/export pdf`, `/model …`, `/provider …`, `/tables`, `/schema`, `/stop`, `/retry`, `/continue`); registry in `src/commands.ts`; keyboard-only nav (↑/↓/Enter/Esc) |
| `DashboardCanvas` | `title`, `subtitle?`, `layout: LayoutRow[]`, `visuals: RenderedVisual[]` | Renders one CSS-grid row per `LayoutRow`, spanning each visual across `slot.width` of 12 columns; KPIs go through `KPICard`, charts through `<ReactECharts>`, tables through `DataTable` |
| `KPICard` | `kpi: KPIPayload`, `title?`, `subtitle?`, `previousValue?`, `compact?` | Big-number tile with optional unit, optional delta vs prior value, optional inline sparkline (ECharts) |
| `InsightSection` | `insight: InsightReport`, `critique?: CritiqueReport \| null` | Renders the structured InsightReport (headline, exec summary, findings, anomalies, recos) as markdown; Critic warnings strip below |
| `AgentProgress` | `steps: AgentStep[]`, `intentInfo?`, `planInfo?`, `queryProgress?`, `isRunning` | Live per-stage timeline (intent → plan → queries → viz → insight) with per-stage latency; legacy tool-call icons retained for the non-streaming `/query` path |
| `InsightPanel` | `content: string`, `usage?: PipelineUsage`, `isStreaming?: bool` | Legacy chat-style markdown panel; renders the `↑in ↓out tok · latency` strip |

### 2.6 TypeScript Types (`types/index.ts`)

```typescript
type DbType      = 'sqlite' | 'postgresql' | 'csv' | 'excel'
type LlmProvider = 'groq' | 'ollama'

interface Session {
  session_id: string
  type: DbType
  name: string
  tables: string[]
  // optional source-specific fields: path, host, database, file, rows, columns, sheets
}

interface AgentStep {
  type: 'tool_start' | 'tool_end'
  tool: string
  input?: string
  output?: string
}

interface TokenUsage {
  input_tokens:  number
  output_tokens: number
  total_tokens:  number
  latency_ms:    number              // wall-clock for the whole agent run
}

interface ChartResult {
  id: string
  option: Record<string, unknown>    // ECharts option object
  title: string
  sql: string
}

interface TableResult {
  id: string
  columns: string[]
  rows: unknown[][]
  sql: string
  title: string
}

interface AnalysisResult {
  id: string
  question: string
  status: 'running' | 'done' | 'error'
  startedAt: Date

  insight: string                    // accumulated tokens
  charts:  ChartResult[]
  tables:  TableResult[]
  steps:   AgentStep[]

  exportSql?:       string
  exportSessionId?: string
  usage?:           TokenUsage | PipelineUsage
  error?:           string

  // Server identifiers — set when the query is bound to a persisted thread
  conversationId?: string
  messageId?:      string

  // Multi-agent pipeline payloads (all optional; absent for chat-style replies)
  intentInfo?:    IntentInfo
  planInfo?:      PlanInfo
  visuals?:       RenderedVisual[]
  insightReport?: InsightReport
  critique?:      CritiqueReport
  queryProgress?: QueryProgress[]
}

// ── Multi-agent pipeline types (mirror backend Pydantic models) ─────────────

type IntentLabel  = 'greeting' | 'help' | 'simple_qa' | 'metric'
                  | 'exploration' | 'dashboard' | 'report' | 'comparison'
type ExportFormat = 'pdf' | 'excel' | 'csv'

interface IntentInfo {
  intent: IntentLabel
  wants_chart: boolean
  wants_dashboard: boolean
  wants_export: ExportFormat | null
  chart_hints: string[]
  time_window: string | null
  complexity: 'simple' | 'moderate' | 'complex'
  keywords: string[]
  confidence: number
  latency_ms?: number
}

interface LayoutSlot { visual_id: string; width: number }
interface LayoutRow  { slots: LayoutSlot[] }

interface PlanInfo {
  title: string
  description: string
  query_count:  number
  visual_count: number
  layout: LayoutRow[]
  latency_ms?: number
}

interface KPIPayload {
  label: string
  value: unknown
  formatted_value: string
  unit: string | null
  sparkline: number[]
}

interface RenderedVisual {
  visual_id: string
  visual_type: string                // "kpi" | "bar" | "line" | "table" | …
  title: string
  subtitle: string | null
  from_query: string
  kpi: KPIPayload | null
  echarts_option: Record<string, unknown> | null
  table_columns: string[]
  table_rows: unknown[][]
  rows_count: number
  error: string | null
}

interface InsightReport {
  headline: string
  executive_summary: string
  key_findings: string[]
  anomalies: string[]
  recommendations: string[]
  latency_ms?: number
}

interface CritiqueIssue {
  severity: 'info' | 'warning' | 'error'
  category: string
  message: string
  location: string | null
}

interface CritiqueReport {
  passed: boolean
  score: number
  issues: CritiqueIssue[]
  latency_ms?: number
}

interface QueryProgress {
  query_id: string
  purpose?: string
  success?: boolean
  rows_count?: number
  latency_ms?: number
  repaired?: boolean
  error?: string | null
  status: 'pending' | 'running' | 'done' | 'error'
}

interface PipelineUsage {
  input_tokens?:  number              // legacy, not always populated
  output_tokens?: number
  total_tokens?:  number
  latency_ms?:    number
  // Per-agent breakdown (multi-agent pipeline)
  intent_latency_ms?:  number
  plan_latency_ms?:    number
  insight_latency_ms?: number
  total_elapsed_ms?:   number
}

interface Conversation {
  id: string
  title: string
  connection_id: string | null
  model:    string | null
  provider: string | null
  created_at: string | null
  updated_at: string | null
  message_count: number
}

interface PersistedMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  charts:  ChartResult[]
  tables:  TableResult[]
  steps:   AgentStep[]
  usage?:  TokenUsage | null
  export_sql?: string | null
  status: 'running' | 'done' | 'error'
  error?: string | null
  created_at: string | null
}

interface UserPreferences {
  model:    string | null
  provider: LlmProvider | null
  active_connection_id:   string | null
  active_conversation_id: string | null
  updated_at?: string | null
}
```

`InsightPanel` renders `↑{input_tokens} ↓{output_tokens} tok · {latency_ms} ms` (formatted as seconds when ≥ 1000 ms) in the AI Analysis header strip.

---

## Section 3: Infrastructure & Environment

### 3.1 Project Structure

```
d:\semantic-reporting\
├── backend\
│   ├── app\               Python source
│   ├── .venv\             Python 3.11 virtualenv (pip / venv)
│   ├── data\              App metadata DB + .secret_key (gitignored)
│   ├── requirements.txt   pip-installable dependency list
│   ├── .env               Runtime secrets (gitignored)
│   ├── .env.example       Template (committed)
│   ├── expenses.db        Sample SQLite database
│   └── uploads\           Temporary file upload storage (gitignored)
├── frontend\
│   ├── src\               TypeScript source
│   ├── node_modules\      npm packages (gitignored)
│   ├── package.json
│   └── vite.config.ts
├── documentation\
│   ├── HLD.md
│   ├── LLD.md
│   └── PRODUCT_PROGRESS.md
├── .vscode\
│   └── settings.json      Python interpreter path lock
├── docker-compose.yml     Optional Redis service (cache backend)
├── run.bat                Windows one-click start (auto-launches Redis if Docker is available)
├── .gitignore
└── .git\
```

### 3.2 Start Commands

```bash
# Backend (one-time setup)
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Backend (every run)
cd backend
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload

# Optional — start Redis to enable the primary cache backend.
# Either:
#   docker compose up -d              # uses repo-root docker-compose.yml
#   # or
#   docker run -d -p 6379:6379 redis:7-alpine
# Without it, the cache transparently falls back to in-process TTLCache
# (or fakeredis if CACHE_BACKEND=fakeredis in .env).

# Frontend
cd frontend
npm install
npm run dev

# Or use the Windows one-click runner — it auto-starts the
# `datalens-redis` container when Docker is available, opens both
# servers in separate consoles, and falls back to in-memory cache
# silently if Docker isn't installed:
#   run.bat
```

On first start the FastAPI lifespan (`app.main.lifespan`) will:
1. Create `backend/uploads/` and `backend/data/` if missing.
2. Run `init_db()` — creates the `connections`, `conversations`, `messages`, `preferences` tables in `app_db_url` if they don't exist.
3. Probe Redis at `redis_url`. On failure, the cache logs `"in-memory fallback active"` and continues.

### 3.3 Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `GROQ_API_KEY` | — | Yes (for groq) | Groq Cloud API key |
| `LLM_PROVIDER` | `groq` | No | `groq` or `ollama` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | No | Local Ollama server |
| `DEFAULT_MODEL` | `llama-3.3-70b-versatile` | No | Model used when frontend sends `null` |
| `AGENT_MAX_TOKENS` | `512` | No | Max tokens for agent LLM call |
| `SYNTHESIS_MAX_TOKENS` | `2048` | No | Legacy — unused |
| `AGENT_MAX_ITERATIONS` | `2` | No | Max agent→tool loop count |
| `APP_DB_URL` | `sqlite:///./data/app.db` | No | Application metadata DB; switch to `postgresql+psycopg2://…` in prod |
| `APP_DB_ECHO` | `false` | No | SQLAlchemy echo for debugging |
| `CACHE_BACKEND` | `redis` (`.env.example` ships `fakeredis`) | No | `redis` = real Redis at `REDIS_URL` (with in-memory fallback) · `fakeredis` = pure-Python in-process Redis-protocol server (zero setup, recommended for dev/test) · `memory` = skip Redis entirely |
| `REDIS_URL` | `redis://localhost:6379/0` | No | Redis URL for the primary cache tier (only used when `CACHE_BACKEND=redis`) |
| `REDIS_ENABLED` | `true` | No | Set to `false` to skip Redis entirely (in-process only) |
| `CACHE_SCHEMA_TTL` | `86400` | No | Schema cache TTL (seconds) — reserved for future use |
| `CACHE_QUERY_TTL` | `300` | No | Query-result cache TTL (seconds) — reserved for future use |
| `CACHE_OLLAMA_TTL` | `60` | No | Ollama model-list cache TTL |
| `APP_SECRET_KEY` | (auto-generated for dev) | **Yes (prod)** | Fernet key (32-byte urlsafe base64) for encrypting Postgres passwords at rest |
| `MODEL_<ROLE>` / `PROVIDER_<ROLE>` / `MAX_TOKENS_<ROLE>` / `TEMP_<ROLE>` | per-role default | No | Per-agent LLM override. `<ROLE>` ∈ {`INTENT_CLASSIFIER`, `PLANNER`, `SCHEMA`, `SQL_AGENT`, `VIZ_DESIGNER`, `INSIGHT_AGENT`, `CRITIC`}. Empty → fall through to global defaults. See §1.5b. |

### 3.4 Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl + B` / `⌘ + B` | Toggle the sidebar (suppressed inside inputs / textareas / `contentEditable`) |
| `Ctrl + K` / `⌘ + K` | Open the Command Palette |
| `Enter` | (Command Palette) Run highlighted command · (rename row) Commit · (sidebar conversation) Open |
| `Escape` | (Command Palette / rename row) Cancel and close |
| `↑` / `↓` | (Command Palette) Move highlight |
