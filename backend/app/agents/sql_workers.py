"""
SQL Workers — execute the Planner's queries in parallel with adaptive repair.

Each worker follows this flow:
  1. Cache lookup (Redis / in-memory)
  2. Validate SQL is read-only (AST walk via sql_guard)
  3. Execute — on success, cache and return
  4. On failure, run up to two repair strategies in sequence:
       Strategy A — LLM column-fix (targeted: replace bad column/table names)
       Strategy B — LLM full-rewrite (blank-slate: rewrite from purpose + schema)
     Each repaired SQL is validated before execution.
  5. If all strategies fail, return the last error for the orchestrator to handle.

All queries for a plan run concurrently (asyncio.gather).  The orchestrator
checks the aggregate success rate and may trigger a re-plan if too many fail.

Read-only is enforced at two points: before strategy A and before strategy B,
so a malicious or hallucinated repair cannot introduce write operations.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Optional

from langchain_community.utilities import SQLDatabase
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from app.agents.llm_factory import llm_for
from app.agents.planner import AnalysisPlan, PlannedQuery
from app.agents.schema_agent import SchemaContext
from app.cache import cache
from app.config import settings
from app.security.sql_guard import validate_read_only


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class QueryResult(BaseModel):
    """Result of executing one PlannedQuery."""
    query_id: str
    sql: str = Field(description="SQL that was actually run (may be repaired).")
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
    latency_ms: int = 0
    repaired: bool = Field(default=False)
    repair_strategy: Optional[str] = Field(default=None, description="Which repair strategy succeeded.")
    rows_count: int = 0


# Row cap — protects LLM context window and SSE wire
_MAX_ROWS: int = settings.agent_max_tokens * 2 if hasattr(settings, "agent_max_tokens") else 1_000
_MAX_ROWS = min(_MAX_ROWS, 1_000)


# ---------------------------------------------------------------------------
# Repair strategies
# ---------------------------------------------------------------------------

_COLUMN_FIX_PROMPT = """\
You are a senior SQL engineer. A query failed, most likely because of a wrong
column or table name. Rewrite ONLY the problematic identifiers — preserve the
query's logic and structure as much as possible.

RULES
-----
* Read-only: SELECT, WITH, UNION only. Never INSERT/UPDATE/DELETE/DROP/CREATE.
* Use ONLY tables and columns shown in the schema.
* Match the dialect (postgresql vs sqlite).
* Output the corrected SQL only — no markdown, no prose.
"""

_FULL_REWRITE_PROMPT = """\
You are a senior SQL engineer. A query failed and the previous repair attempt
also failed. Rewrite it from scratch using ONLY the original purpose and schema.

RULES
-----
* Read-only: SELECT, WITH, UNION only. Never INSERT/UPDATE/DELETE/DROP/CREATE.
* Use ONLY tables and columns shown in the schema.
* Match the dialect (postgresql vs sqlite).
* Keep the result set small: LIMIT 25 for category breakdowns, 100 for time series.
* Output the corrected SQL only — no markdown, no prose.
"""


def _strip_sql(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip().rstrip(";").strip()


async def _strategy_column_fix(
    bad_sql: str,
    error_msg: str,
    purpose: str,
    schema_ddl: str,
) -> Optional[str]:
    """Strategy A: ask the LLM to fix column/table name errors."""
    llm = llm_for("sql_agent")
    user_msg = (
        f"## Schema\n\n{schema_ddl}\n\n"
        f"## Purpose\n\n{purpose}\n\n"
        f"## Failing SQL\n\n{bad_sql}\n\n"
        f"## Error\n\n{error_msg}\n\n"
        f"Fix the column or table name errors. Output corrected SQL only."
    )
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=_COLUMN_FIX_PROMPT),
            HumanMessage(content=user_msg),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(resp)
        return _strip_sql(str(resp.content))
    except Exception:
        return None


async def _strategy_full_rewrite(
    purpose: str,
    schema_ddl: str,
    all_errors: str,
) -> Optional[str]:
    """Strategy B: full rewrite from purpose + schema, ignoring the broken SQL."""
    llm = llm_for("sql_agent")
    user_msg = (
        f"## Schema\n\n{schema_ddl}\n\n"
        f"## Query purpose\n\n{purpose}\n\n"
        f"## Previous errors (for context only)\n\n{all_errors}\n\n"
        f"Write a new, correct SQL query that achieves the purpose. "
        f"Output the SQL only."
    )
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=_FULL_REWRITE_PROMPT),
            HumanMessage(content=user_msg),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(resp)
        return _strip_sql(str(resp.content))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(session_id: str, sql: str) -> str:
    h = hashlib.sha256(sql.strip().encode()).hexdigest()[:16]
    return f"queryres:{session_id}:{h}"


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _to_jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _execute_sync(db: SQLDatabase, sql: str) -> tuple[list[str], list[list[Any]]]:
    """Synchronous SQL execution — run inside a thread executor."""
    with db._engine.connect() as conn:
        rs = conn.execute(sa_text(sql))
        cols = list(rs.keys())
        rows = [list(r) for r in rs.fetchmany(_MAX_ROWS + 1)]
    if len(rows) > _MAX_ROWS:
        rows = rows[:_MAX_ROWS]
    rows = [[_to_jsonable(v) for v in row] for row in rows]
    return cols, rows


# ---------------------------------------------------------------------------
# Single-query worker with adaptive repair
# ---------------------------------------------------------------------------

async def run_one_query(
    pq: PlannedQuery,
    db: SQLDatabase,
    session_id: Optional[str],
    schema_ddl: str,
) -> QueryResult:
    """
    Execute one PlannedQuery with up to two repair strategies on failure.

    Flow:
      cache hit  →  return cached
      validate   →  block if not read-only (no repair; write intent is rejected)
      execute    →  success → cache + return
      fail       →  Strategy A (column fix) → execute
      fail       →  Strategy B (full rewrite) → execute
      fail       →  return final error
    """
    t0 = time.perf_counter()

    # Cache lookup
    cache_k = _cache_key(session_id or "anon", pq.sql) if session_id else None
    if cache_k:
        cached = await cache.aget(cache_k)
        if cached is not None:
            try:
                r = QueryResult.model_validate(cached)
                return r.model_copy(update={"latency_ms": int((time.perf_counter() - t0) * 1000)})
            except Exception:
                pass

    sql_to_run = pq.sql

    # Read-only guard (only the planner's SQL; repaired SQL is re-checked below)
    try:
        validate_read_only(sql_to_run)
    except ValueError as exc:
        return QueryResult(
            query_id=pq.id, sql=sql_to_run, success=False,
            error=f"Read-only guard blocked this query: {exc}",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    loop = asyncio.get_event_loop()
    errors: list[str] = []

    # First execution attempt
    try:
        cols, rows = await loop.run_in_executor(None, _execute_sync, db, sql_to_run)
    except Exception as exc:
        errors.append(str(exc).splitlines()[0][:400])
    else:
        result = QueryResult(
            query_id=pq.id, sql=sql_to_run, success=True,
            columns=cols, rows=rows, rows_count=len(rows),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        if cache_k:
            await cache.aset(cache_k, result.model_dump(mode="json"), ttl=settings.cache_query_ttl)
        return result

    # Strategy A — column / table name fix
    repaired_a = await _strategy_column_fix(
        bad_sql=sql_to_run,
        error_msg=errors[-1],
        purpose=pq.purpose,
        schema_ddl=schema_ddl,
    )
    if repaired_a and repaired_a.strip() != sql_to_run.strip():
        try:
            validate_read_only(repaired_a)
            cols, rows = await loop.run_in_executor(None, _execute_sync, db, repaired_a)
        except Exception as exc:
            errors.append(f"Strategy A: {str(exc).splitlines()[0][:400]}")
        else:
            result = QueryResult(
                query_id=pq.id, sql=repaired_a, success=True,
                columns=cols, rows=rows, rows_count=len(rows),
                repaired=True, repair_strategy="column_fix",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            if cache_k:
                await cache.aset(cache_k, result.model_dump(mode="json"), ttl=settings.cache_query_ttl)
            return result
    else:
        errors.append("Strategy A produced no different SQL.")

    # Strategy B — full rewrite
    repaired_b = await _strategy_full_rewrite(
        purpose=pq.purpose,
        schema_ddl=schema_ddl,
        all_errors="\n".join(errors),
    )
    if repaired_b and repaired_b.strip() not in (sql_to_run.strip(), (repaired_a or "").strip()):
        try:
            validate_read_only(repaired_b)
            cols, rows = await loop.run_in_executor(None, _execute_sync, db, repaired_b)
        except Exception as exc:
            errors.append(f"Strategy B: {str(exc).splitlines()[0][:400]}")
        else:
            result = QueryResult(
                query_id=pq.id, sql=repaired_b, success=True,
                columns=cols, rows=rows, rows_count=len(rows),
                repaired=True, repair_strategy="full_rewrite",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            if cache_k:
                await cache.aset(cache_k, result.model_dump(mode="json"), ttl=settings.cache_query_ttl)
            return result
    else:
        errors.append("Strategy B produced no different SQL.")

    # All strategies exhausted
    return QueryResult(
        query_id=pq.id, sql=sql_to_run, success=False,
        error=" | ".join(errors[-2:]),
        repaired=True,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# Parallel execution over a plan
# ---------------------------------------------------------------------------

async def run_planned_queries(
    plan: AnalysisPlan,
    db: SQLDatabase,
    schema: SchemaContext,
    session_id: Optional[str] = None,
) -> dict[str, QueryResult]:
    """
    Execute every PlannedQuery concurrently.

    Returns ``{query_id: QueryResult}``.  Failed queries are included so
    downstream agents and the orchestrator can decide how to react (e.g.
    drop a visual whose source query failed, or trigger a re-plan).
    """
    if not plan.queries:
        return {}
    coros = [run_one_query(pq, db, session_id, schema.ddl) for pq in plan.queries]
    results = await asyncio.gather(*coros, return_exceptions=False)
    return {r.query_id: r for r in results}
