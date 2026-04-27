# Low-Level Design — Semantic Reporting (NL-DB Query)

> Last updated: 2026-04-27 (rev. token+latency telemetry, persistence, cache, crypto)

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
│   │   └── sql_agent.py              LangGraph ReAct graph + token/latency telemetry
│   ├── cache/
│   │   ├── __init__.py               Re-exports `cache` singleton
│   │   └── cache.py                  Two-tier cache (Redis → in-process TTLCache)
│   ├── db/
│   │   ├── manager.py                ConnectionManager — in-memory live engine registry
│   │   ├── app_db.py                 SQLAlchemy 2.0 engine + session_scope (app metadata)
│   │   ├── models.py                 ORM models: Connection, Conversation, Message, Preference
│   │   └── repositories.py           Repository classes wrapping all SQL access
│   ├── security/
│   │   ├── sql_guard.py              AST-level read-only SQL validation (sqlglot)
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
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

### 1.5 `app/agents/sql_agent.py` — LangGraph ReAct Agent

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
├── App.tsx                Root layout; runs hydration + preference-sync hooks
├── index.css              Tailwind directives + custom scrollbar
├── api/
│   └── client.ts          Typed fetch wrappers — connections, conversations,
│                          preferences, exports, streaming
├── store/
│   └── index.ts           Zustand store + persist middleware (localStorage)
├── types/
│   └── index.ts           TypeScript interfaces (Session, AnalysisResult,
│                          TokenUsage, Conversation, PersistedMessage, ...)
├── hooks/
│   ├── useAnalysis.ts     SSE stream hook — drives a single query run
│   └── useHydrate.ts      useHydrate / useConversationSync / usePreferenceSync
└── components/
    ├── Header.tsx          Top bar: title, model picker, provider toggle
    ├── Sidebar.tsx         Conversations list + new-chat + connection list
    ├── ConnectionPanel.tsx DB connect / upload form
    ├── QueryBar.tsx        Natural language input + submit
    ├── AnalysisCard.tsx    Renders one streamed/persisted analysis
    ├── EChartCard.tsx      Interactive ECharts wrapper
    ├── DataTable.tsx       Tabular query result display
    ├── AgentProgress.tsx   Live tool-call step indicator
    └── InsightPanel.tsx    Streaming-text panel; renders ↑in ↓out tok · latency
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

Drives one streamed query run end-to-end.

```typescript
async function runQuery(question: string): Promise<void>
```

Flow:
1. `POST /api/v1/query/stream` with `session_id + question + model + provider + conversation_id`
2. Read `response.body` as `ReadableStream`, split on `\n`, parse `data: {...}` lines
3. Dispatch to the store based on `event.type`:
   - `conversation` → `attachServerIds(cardId, conversation_id, assistant_message_id)`; `upsertConversation` if new
   - `token`        → `appendToken(cardId, content)`
   - `chart_spec`   → `addChart(cardId, {id, option, title, sql})`
   - `table_data`   → `addTable(cardId, {id, columns, rows, sql, title})`
   - `tool_start`/`tool_end` → `addStep(cardId, …)`
   - `export_ctx`   → `setExportCtx(cardId, sql, session_id)`
   - `usage`        → `setUsage(cardId, {input_tokens, output_tokens, total_tokens, latency_ms})`
   - `error`        → `setAnalysisError(cardId, content)`
   - `done`         → `finalizeAnalysis(cardId)`

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
api.streamQuery(sessionId, question, model, provider, callbacks): () => void
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
| `Sidebar` | reads `sessions[]`, `conversations[]`, `activeSessionId`, `activeConversationId` from the store | ChatGPT-style nav: top-pinned **New chat** button + collapsible **Connect database** panel; conversations are grouped into `Today` / `Yesterday` / `Previous 7 days` / `Older` buckets via `groupByDate(updated_at)`; each row supports inline rename (Enter to commit, Escape to cancel) and confirm-then-delete; connections live in a separate collapsible section beneath the conversation list |
| `InsightPanel` | `tables: string[]`, `metadata: SessionMeta` | Schema browser; also renders the `↑in ↓out tok · latency` strip |

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
  usage?:           TokenUsage       // rendered as "↑X ↓Y tok · Zms" in InsightPanel
  error?:           string

  // Server identifiers — set when the query is bound to a persisted thread
  conversationId?: string
  messageId?:      string
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
