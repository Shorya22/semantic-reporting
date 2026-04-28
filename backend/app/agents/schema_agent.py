"""
Schema Agent — caches DDL and data-profiles each connected database.

Why this exists
---------------
The Planner and SQL Workers write better SQL when they know more than just
the table names. Cardinalities, sample values, and value ranges let them:

* pick the right chart type (pie chart only if cardinality ≤ 8)
* write tighter WHERE filters using real example values
* avoid SELECT * on huge tables

This agent runs once per session (cached in Redis) and returns a compact
``SchemaContext`` consumed by every downstream agent in the pipeline.

Cost / latency
--------------
Profiling is mostly N small SQL queries (one COUNT(*) per table + one
information_schema/PRAGMA per table). Sub-second on a 1M-row Postgres DB
when run in parallel. Result is cached for ``settings.cache_schema_ttl``
seconds (default 1 day).
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from langchain_community.utilities import SQLDatabase
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from app.cache import cache
from app.config import settings


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class ColumnProfile(BaseModel):
    name: str
    dtype: str
    nullable: bool = True
    distinct_count: Optional[int] = None
    sample_values: list[Any] = Field(default_factory=list)
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None


class TableProfile(BaseModel):
    name: str
    row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)


class SchemaContext(BaseModel):
    """Everything downstream agents need to know about the database."""
    dialect: str = Field(description="sqlite | postgresql")
    ddl: str = Field(description="Compact DDL (one line per column).")
    profiles: dict[str, TableProfile] = Field(
        default_factory=dict,
        description="One TableProfile per usable table.",
    )
    summary: str = Field(
        default="",
        description="Natural-language overview useful for prompt context.",
    )


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_key(session_id: str) -> str:
    return f"schema:context:{session_id}"


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

# How many distinct values to sample per column (capped to keep prompts small).
_SAMPLE_LIMIT = 5

# Skip profiling tables larger than this (just record row count).
_PROFILE_ROW_LIMIT = 5_000_000


def _detect_dialect(db: SQLDatabase) -> str:
    name = db.dialect
    if name and name.lower().startswith("postgres"):
        return "postgresql"
    return name or "unknown"


def _list_tables(db: SQLDatabase) -> list[str]:
    return list(db.get_usable_table_names())


def _row_count(db: SQLDatabase, table: str) -> int:
    try:
        with db._engine.connect() as conn:
            r = conn.execute(sa_text(f'SELECT COUNT(*) FROM "{table}"'))
            return int(r.scalar() or 0)
    except Exception:
        return 0


def _columns_for(db: SQLDatabase, table: str, dialect: str) -> list[tuple[str, str, bool]]:
    """Return [(column_name, dtype, nullable)] for one table."""
    rows: list[tuple[str, str, bool]] = []
    try:
        with db._engine.connect() as conn:
            if dialect == "postgresql":
                q = sa_text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns WHERE table_name = :t "
                    "ORDER BY ordinal_position"
                )
                for r in conn.execute(q, {"t": table}).fetchall():
                    rows.append((str(r[0]), str(r[1]), str(r[2]).lower() == "yes"))
            else:  # sqlite
                q = sa_text(f'PRAGMA table_info("{table}")')
                for r in conn.execute(q).fetchall():
                    # cid, name, type, notnull, dflt_value, pk
                    rows.append((str(r[1]), str(r[2]), int(r[3]) == 0))
    except Exception:
        pass
    return rows


def _is_numeric(dtype: str) -> bool:
    d = (dtype or "").lower()
    return any(k in d for k in (
        "int", "numeric", "decimal", "double", "float", "real", "money",
    ))


def _is_textual(dtype: str) -> bool:
    d = (dtype or "").lower()
    return any(k in d for k in ("char", "text", "string"))


def _is_temporal(dtype: str) -> bool:
    d = (dtype or "").lower()
    return any(k in d for k in ("date", "time", "timestamp"))


def _profile_column(
    db: SQLDatabase,
    table: str,
    col: str,
    dtype: str,
) -> tuple[Optional[int], list[Any], Optional[Any], Optional[Any]]:
    """Return (distinct_count, sample_values, min, max)."""
    distinct: Optional[int] = None
    sample: list[Any] = []
    mn: Optional[Any] = None
    mx: Optional[Any] = None

    try:
        with db._engine.connect() as conn:
            if _is_textual(dtype):
                # Distinct count for low-cardinality text only (cheap)
                r = conn.execute(sa_text(
                    f'SELECT COUNT(DISTINCT "{col}") FROM "{table}"'
                ))
                distinct = int(r.scalar() or 0)
                if 0 < distinct <= 50:
                    rs = conn.execute(sa_text(
                        f'SELECT DISTINCT "{col}" FROM "{table}" '
                        f'WHERE "{col}" IS NOT NULL LIMIT {_SAMPLE_LIMIT}'
                    ))
                    sample = [row[0] for row in rs.fetchall()]
            elif _is_numeric(dtype):
                rs = conn.execute(sa_text(
                    f'SELECT MIN("{col}"), MAX("{col}") FROM "{table}"'
                ))
                row = rs.fetchone()
                if row:
                    mn, mx = row[0], row[1]
            elif _is_temporal(dtype):
                rs = conn.execute(sa_text(
                    f'SELECT MIN("{col}"), MAX("{col}") FROM "{table}"'
                ))
                row = rs.fetchone()
                if row:
                    mn, mx = (str(row[0]) if row[0] is not None else None,
                              str(row[1]) if row[1] is not None else None)
    except Exception:
        pass

    return distinct, sample, mn, mx


def _profile_table(db: SQLDatabase, table: str, dialect: str) -> TableProfile:
    row_count = _row_count(db, table)
    cols = _columns_for(db, table, dialect)

    columns: list[ColumnProfile] = []
    if row_count <= _PROFILE_ROW_LIMIT:
        for name, dtype, nullable in cols:
            d, s, mn, mx = _profile_column(db, table, name, dtype)
            columns.append(ColumnProfile(
                name=name, dtype=dtype, nullable=nullable,
                distinct_count=d, sample_values=[_to_jsonable(v) for v in s],
                min_value=_to_jsonable(mn), max_value=_to_jsonable(mx),
            ))
    else:
        # Skip per-column profiling for very large tables — just list shape
        for name, dtype, nullable in cols:
            columns.append(ColumnProfile(name=name, dtype=dtype, nullable=nullable))

    return TableProfile(name=table, row_count=row_count, columns=columns)


def _to_jsonable(v: Any) -> Any:
    """Convert non-JSON-serialisable values (Decimal, datetime, …) to str."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


# ---------------------------------------------------------------------------
# Compact DDL + summary text
# ---------------------------------------------------------------------------

def _compact_ddl(profiles: dict[str, TableProfile]) -> str:
    """One-line-per-column DDL: easier for LLMs than CREATE TABLE blobs."""
    lines: list[str] = []
    for tbl in profiles.values():
        lines.append(f"-- {tbl.name}  ({tbl.row_count:,} rows)")
        for c in tbl.columns:
            extras: list[str] = []
            if c.distinct_count is not None:
                extras.append(f"distinct={c.distinct_count}")
            if c.sample_values:
                shown = ", ".join(repr(v)[:30] for v in c.sample_values[:3])
                extras.append(f"sample=[{shown}]")
            if c.min_value is not None or c.max_value is not None:
                extras.append(f"range=[{c.min_value}..{c.max_value}]")
            extra_str = "  -- " + " ".join(extras) if extras else ""
            null_str = "" if c.nullable else " NOT NULL"
            lines.append(f"  {c.name:<28s} {c.dtype}{null_str}{extra_str}")
        lines.append("")
    return "\n".join(lines)


def _summary_text(dialect: str, profiles: dict[str, TableProfile]) -> str:
    parts = [f"{dialect.title()} database with {len(profiles)} tables."]
    biggest = sorted(profiles.values(), key=lambda t: t.row_count, reverse=True)[:3]
    if biggest:
        parts.append("Largest tables: " + ", ".join(
            f"{t.name} ({t.row_count:,})" for t in biggest
        ))
    # Detect time columns for the planner's awareness
    time_cols: list[str] = []
    for t in profiles.values():
        for c in t.columns:
            if _is_temporal(c.dtype):
                time_cols.append(f"{t.name}.{c.name}")
    if time_cols:
        parts.append("Time columns: " + ", ".join(time_cols[:8])
                     + ("…" if len(time_cols) > 8 else ""))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_schema_context(
    session_id: str,
    db: SQLDatabase,
    *,
    refresh: bool = False,
) -> SchemaContext:
    """Return the cached SchemaContext for ``session_id``, building it if absent.

    Set ``refresh=True`` to bypass the cache (useful after schema migrations).
    """
    key = _cache_key(session_id)
    if not refresh:
        cached = await cache.aget(key)
        if cached is not None:
            try:
                return SchemaContext.model_validate(cached)
            except Exception:
                pass  # cache poisoned; fall through to rebuild

    ctx = await asyncio.get_event_loop().run_in_executor(
        None, _build_context_sync, db
    )
    await cache.aset(key, ctx.model_dump(mode="json"), ttl=settings.cache_schema_ttl)
    return ctx


def _build_context_sync(db: SQLDatabase) -> SchemaContext:
    dialect = _detect_dialect(db)
    profiles: dict[str, TableProfile] = {}
    for table in _list_tables(db):
        profiles[table] = _profile_table(db, table, dialect)

    return SchemaContext(
        dialect=dialect,
        ddl=_compact_ddl(profiles),
        profiles=profiles,
        summary=_summary_text(dialect, profiles),
    )


def invalidate_schema_cache(session_id: str) -> None:
    """Drop the cached schema for one session (called on disconnect / reload)."""
    cache.delete(_cache_key(session_id))
