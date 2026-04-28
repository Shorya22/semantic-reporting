"""
Planner agent — decomposes a user question into a typed AnalysisPlan.

The plan is the single source of truth for everything downstream:
  * SQL Workers execute each ``PlannedQuery`` in parallel.
  * Viz Designer turns each ``PlannedVisual`` into an ECharts option / KPI card.
  * Layout drives the Dashboard renderer and the PDF/XLSX report composer.

Output schema (JSON-friendly via Pydantic) is stable so frontends and the
Report Composer can rely on it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agents.intent_classifier import Intent
from app.agents.llm_factory import llm_for


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

VisualType = Literal[
    "kpi",            # single big number with optional delta + sparkline
    "bar",            # categorical comparison
    "horizontal_bar", # long category names
    "line",           # time series
    "area",           # cumulative time series
    "pie",            # proportion ≤ 8 categories
    "donut",          # KPI-style proportion
    "scatter",        # 2 numeric correlation
    "funnel",         # stage drop-off
    "treemap",        # many categories proportion
    "gauge",          # single value vs scale
    "table",          # raw tabular drill-down
]


class PlannedQuery(BaseModel):
    """One SQL query the plan needs executed."""
    id: str = Field(
        description="Stable short identifier, sequential: 'q1', 'q2', 'q3', …"
    )
    purpose: str = Field(
        description=(
            "One business-level sentence explaining what business question this "
            "query answers (not SQL jargon)."
        )
    )
    sql: str = Field(
        description=(
            "Complete, executable SQL SELECT statement. Read-only only: "
            "SELECT/WITH/UNION. Never INSERT/UPDATE/DELETE/DROP/CREATE. "
            "Always alias aggregates (SUM(x) AS total)."
        )
    )
    expected_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Exact column names / aliases that the SELECT will produce; "
            "these become x_col and y_col for charts."
        ),
    )


class PlannedVisual(BaseModel):
    """One visual derived from a PlannedQuery."""
    id: str = Field(
        description="Stable short identifier, sequential: 'v1', 'v2', …"
    )
    type: VisualType = Field(
        description=(
            "Chart type — choose the best fit for the data shape "
            "(see planning rules)."
        )
    )
    from_query: str = Field(
        description="Must match an existing PlannedQuery.id exactly."
    )
    title: str = Field(
        description=(
            "Human-readable chart title shown above the visual "
            "(business language, not technical)."
        )
    )
    x_col: Optional[str] = Field(
        default=None,
        description=(
            "Column name from expected_columns of the source query used as "
            "the category/x axis; null for KPI."
        ),
    )
    y_col: Optional[str] = Field(
        default=None,
        description=(
            "Column name from expected_columns of the source query used as "
            "the numeric measure/y axis."
        ),
    )
    color_col: Optional[str] = Field(
        default=None,
        description=(
            "Optional column for color grouping / series split; "
            "null if not needed."
        ),
    )
    subtitle: Optional[str] = Field(
        default=None,
        description=(
            "Optional short secondary text shown below the title; "
            "null if not needed."
        ),
    )
    unit: Optional[str] = Field(
        default=None,
        description=(
            "Display unit for the primary measure: '%', 'ms', 'INR', 'count', "
            "etc.; null if dimensionless."
        ),
    )


class LayoutSlot(BaseModel):
    """One cell in a dashboard row."""
    visual_id: str = Field(
        description="Must match an existing PlannedVisual.id exactly."
    )
    width: int = Field(
        default=12, ge=1, le=12,
        description=(
            "Bootstrap-style column width 1–12; all slots in a row should "
            "sum to 12."
        ),
    )


class LayoutRow(BaseModel):
    """One row of the dashboard grid."""
    slots: list[LayoutSlot] = Field(
        description="Cells from left to right; widths sum to 12."
    )


class AnalysisPlan(BaseModel):
    """The full plan the orchestrator executes."""
    title: str = Field(
        description="3–7 word title used as the report/dashboard header."
    )
    description: str = Field(
        description=(
            "1–2 sentence executive summary of what this analysis answers."
        ),
    )
    queries: list[PlannedQuery] = Field(
        default_factory=list,
        description=(
            "1–8 SQL queries; combine related metrics, avoid near-duplicate queries."
        ),
    )
    visuals: list[PlannedVisual] = Field(
        default_factory=list,
        description=(
            "1–8 visuals derived from queries; each must reference an existing "
            "query id."
        ),
    )
    layout: list[LayoutRow] = Field(
        default_factory=list,
        description=(
            "Dashboard grid rows top-to-bottom; widths in each row must sum to 12."
        ),
    )
    latency_ms: int = Field(default=0)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior data analyst. Decompose the user's question into a structured
Analysis Plan that downstream agents will execute.

PLANNING RULES
==============

A. SQL WRITING
  * Use ONLY tables and columns present in the schema below.
  * Read-only: SELECT, WITH, UNION. Never INSERT/UPDATE/DELETE/DROP/CREATE.
  * Always alias aggregates: SUM(amount) AS total, COUNT(*) AS count_records
  * PostgreSQL time bucketing: date_trunc('month', col)
  * SQLite time bucketing: strftime('%Y-%m', col)
  * "Top N" queries: always include ORDER BY … DESC LIMIT N
  * Row limits: 10–25 rows for bar/pie charts; up to 100 for time series

B. VISUAL TYPE SELECTION
  * Single number                              → kpi   (no x_col)
  * Time vs numeric                            → line or area
  * Category vs numeric, ≤12 categories        → bar
  * Category vs numeric, long names            → horizontal_bar
  * Part-to-whole, ≤8 categories               → pie or donut
  * Stage drop-off                             → funnel
  * Hierarchical proportions                   → treemap
  * Single % vs target                         → gauge
  * Drill-down / raw rows                      → table
  * Two numeric correlation                    → scatter

C. PLAN SIZE BY INTENT
  * simple_qa / metric    → 1 query, 1 visual (kpi or table)
  * exploration/comparison → 2–3 queries, 2–3 visuals
  * dashboard             → 4–7 queries, 4–7 visuals, row 1 = KPI strip
  * report                → 6–10 queries, 6–10 visuals

D. LAYOUT PATTERNS
  * KPI strip       → one row, 3–4 slots width=3 or 4
  * Hero chart      → width=12 solo row
  * Side-by-side    → two slots width=6 each
  * Three-up        → three slots width=4 each
  * Tables          → width=12 on own row

E. REFERENTIAL INTEGRITY (strictly enforced)
  * PlannedVisual.from_query must match a PlannedQuery.id
  * visual_id in layout must match a PlannedVisual.id
  * x_col and y_col must be in the source query's expected_columns

F. EFFICIENCY
  * Combine related metrics into one query where possible
  * Do not fetch raw rows when you can aggregate server-side
"""

_USER_TEMPLATE = """\
## Database schema

{schema_ddl}

## Detected intent

{intent_json}

## User question

{question}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _parse_json_lenient(text: str) -> Optional[dict]:
    if not text:
        return None
    s = text.strip()
    s = _FENCE_RE.sub("", s).strip()
    if not s.startswith("{"):
        first, last = s.find("{"), s.rfind("}")
        if first == -1 or last == -1:
            return None
        s = s[first:last + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _truncate_schema(schema_ddl: Optional[str], max_chars: int = 8000) -> str:
    """Trim very large schema DDLs so the planner prompt stays under context."""
    if not schema_ddl:
        return "(schema not available — use schema introspection if necessary)"
    if len(schema_ddl) <= max_chars:
        return schema_ddl
    return schema_ddl[:max_chars] + f"\n\n-- [truncated, {len(schema_ddl) - max_chars} chars omitted]"


def _validate_plan(plan: AnalysisPlan) -> AnalysisPlan:
    """Repair common referential mistakes the LLM might make."""
    query_ids = {q.id for q in plan.queries}
    visual_ids = {v.id for v in plan.visuals}

    # Drop visuals whose source query is missing
    plan.visuals = [v for v in plan.visuals if v.from_query in query_ids]
    visual_ids = {v.id for v in plan.visuals}

    # Drop layout slots that reference unknown visuals
    cleaned_layout: list[LayoutRow] = []
    for row in plan.layout:
        kept = [s for s in row.slots if s.visual_id in visual_ids]
        if kept:
            cleaned_layout.append(LayoutRow(slots=kept))
    plan.layout = cleaned_layout

    # If the LLM forgot the layout entirely, build a sensible default:
    # KPIs (if any) on row 1, then 2-up grid for charts, tables full-width.
    if not plan.layout and plan.visuals:
        kpis   = [v for v in plan.visuals if v.type == "kpi"]
        charts = [v for v in plan.visuals if v.type not in ("kpi", "table")]
        tables = [v for v in plan.visuals if v.type == "table"]

        if kpis:
            slot_w = max(3, 12 // max(1, len(kpis)))
            plan.layout.append(LayoutRow(slots=[
                LayoutSlot(visual_id=v.id, width=min(slot_w, 12)) for v in kpis[:4]
            ]))

        for i in range(0, len(charts), 2):
            pair = charts[i:i + 2]
            if len(pair) == 1:
                plan.layout.append(LayoutRow(
                    slots=[LayoutSlot(visual_id=pair[0].id, width=12)]
                ))
            else:
                plan.layout.append(LayoutRow(slots=[
                    LayoutSlot(visual_id=pair[0].id, width=6),
                    LayoutSlot(visual_id=pair[1].id, width=6),
                ]))

        for tbl in tables:
            plan.layout.append(LayoutRow(slots=[LayoutSlot(visual_id=tbl.id, width=12)]))

    return plan


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def plan_analysis(
    question: str,
    intent: Intent,
    schema_ddl: Optional[str],
) -> AnalysisPlan:
    """Produce a full AnalysisPlan for ``question`` given ``intent`` + schema.

    Returns an empty plan with title=question on parse failure — the
    orchestrator can decide to fall back to a single SQL agent run.
    """
    t0 = time.perf_counter()
    llm = llm_for("planner")
    structured_llm = llm.with_structured_output(AnalysisPlan, include_raw=True)

    user_msg = _USER_TEMPLATE.format(
        schema_ddl=_truncate_schema(schema_ddl),
        intent_json=intent.model_dump_json(indent=2),
        question=question.strip(),
    )

    plan: Optional[AnalysisPlan] = None
    raw_text: str = ""

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(result["raw"])

        if result["parsed"] is not None and result["parsing_error"] is None:
            plan = result["parsed"]
        else:
            raw_text = str(result["raw"].content)
    except Exception as exc:
        return AnalysisPlan(
            title=question[:60] or "Analysis",
            description=f"Planner unavailable ({exc.__class__.__name__}); falling back.",
            queries=[],
            visuals=[],
            layout=[],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    # Fallback: lenient JSON parse + best-effort coerce
    if plan is None:
        parsed = _parse_json_lenient(raw_text)
        if parsed is None:
            return AnalysisPlan(
                title=question[:60] or "Analysis",
                description="Planner output could not be parsed; falling back.",
                queries=[],
                visuals=[],
                layout=[],
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        try:
            plan = AnalysisPlan.model_validate(parsed)
        except ValidationError:
            # Best-effort coerce — be very defensive about each item
            def _safe_query(q: dict) -> Optional[PlannedQuery]:
                try:
                    if not q.get("id") or not q.get("sql"):
                        return None
                    return PlannedQuery(
                        id=str(q["id"]), sql=str(q["sql"]),
                        purpose=str(q.get("purpose", "")),
                        expected_columns=list(q.get("expected_columns") or []),
                    )
                except Exception:
                    return None

            def _safe_visual(v: dict) -> Optional[PlannedVisual]:
                try:
                    if not v.get("id") or not v.get("from_query") or not v.get("type"):
                        return None
                    return PlannedVisual.model_validate(v)
                except Exception:
                    return None

            def _safe_row(r: dict) -> Optional[LayoutRow]:
                slots: list[LayoutSlot] = []
                for s in (r.get("slots") or []):
                    try:
                        if not s or not s.get("visual_id"):
                            continue
                        slots.append(LayoutSlot(
                            visual_id=str(s["visual_id"]),
                            width=int(s.get("width") or 12),
                        ))
                    except Exception:
                        continue
                return LayoutRow(slots=slots) if slots else None

            plan = AnalysisPlan(
                title=parsed.get("title") or question[:60],
                description=parsed.get("description") or "",
                queries=[q for q in (_safe_query(q) for q in (parsed.get("queries") or [])) if q],
                visuals=[v for v in (_safe_visual(v) for v in (parsed.get("visuals") or [])) if v],
                layout=[r for r in (_safe_row(r) for r in (parsed.get("layout") or [])) if r],
            )

    plan = _validate_plan(plan)
    return plan.model_copy(update={"latency_ms": int((time.perf_counter() - t0) * 1000)})
