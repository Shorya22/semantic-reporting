"""
Viz Designer — converts (PlannedVisual, QueryResult) into a renderable visual.

Outputs one of three artifact shapes (depending on ``visual.type``):

* **KPI**   – a single number + label + optional unit + delta + sparkline
* **Chart** – an ECharts option dict (handed to ``echarts-for-react``)
* **Table** – column headers + data rows

The design is deterministic — no LLM calls. The Planner has already chosen
the visual type and which columns to use; this agent just converts those
decisions into the final wire format.

When the Planner's column hints are missing or wrong (e.g. a chart says
``x_col="month"`` but the query produced ``"period"``), the Designer falls
back to data-shape heuristics so the visual still renders.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.planner import PlannedVisual
from app.agents.sql_workers import QueryResult
from app.services.viz_service import ChartSpec, build_echarts_option


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class KPIPayload(BaseModel):
    """A single big-number KPI card."""
    label: str
    value: Any                       # raw value (number or string)
    formatted_value: str             # display-ready: "1.2M", "94.3%", "INR 4.5M"
    unit: Optional[str] = None
    sparkline: list[float] = Field(default_factory=list,
                                   description="Optional time-series for tiny inline chart.")


class RenderedVisual(BaseModel):
    """Final, ready-to-render visual artifact."""
    visual_id: str
    type: str                        # echoes PlannedVisual.type
    title: str
    subtitle: Optional[str] = None

    # Filled when type == "kpi"
    kpi: Optional[KPIPayload] = None

    # Filled when type is a chart
    echarts_option: Optional[dict] = None

    # Filled when type == "table"
    table_columns: list[str] = Field(default_factory=list)
    table_rows: list[list[Any]] = Field(default_factory=list)

    # Provenance / diagnostics
    from_query: str
    sql: str = ""
    rows_count: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def _format_number(v: Any, unit: Optional[str]) -> str:
    """Format a value for KPI display: 12500 -> '12.5K', 0.943 -> '94.3%' …"""
    if v is None:
        return "—"

    # Pass through strings (e.g. dates already serialised by the worker)
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)

    u = (unit or "").lower()
    if u in ("%", "percent"):
        # If value looks like a fraction (0..1), scale to 0..100
        if -1.5 <= n <= 1.5 and abs(n) <= 1:
            n *= 100
        return f"{n:,.1f}%"
    if u == "ms":
        if n >= 1000:
            return f"{n / 1000:,.2f} s"
        return f"{n:,.0f} ms"
    if u in ("inr", "rupee", "rupees", "₹"):
        return f"₹{_short_number(n)}"
    if u in ("usd", "$"):
        return f"${_short_number(n)}"
    if u in ("count", "records", "transactions") or u == "":
        return _short_number(n)
    return f"{_short_number(n)} {unit}"


def _short_number(n: float) -> str:
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:,.2f}B"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:,.2f}M"
    if abs_n >= 10_000:
        return f"{n / 1_000:,.1f}K"
    if abs_n >= 1 and n.is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}"


# ---------------------------------------------------------------------------
# Heuristics for fallback when planner's column hints don't match results
# ---------------------------------------------------------------------------

def _is_numeric_column(rows: list[list[Any]], col_idx: int) -> bool:
    seen = 0
    for row in rows[:50]:
        if col_idx >= len(row):
            continue
        v = row[col_idx]
        if v is None:
            continue
        seen += 1
        try:
            float(v)
        except (TypeError, ValueError):
            return False
    return seen > 0


def _pick_x_y(columns: list[str], rows: list[list[Any]],
              hint_x: Optional[str], hint_y: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve x_col / y_col, preferring planner hints when valid."""
    x = hint_x if (hint_x and hint_x in columns) else None
    y = hint_y if (hint_y and hint_y in columns) else None

    if x is None or y is None:
        # First non-numeric column = x, first numeric column = y
        for i, c in enumerate(columns):
            if y is None and _is_numeric_column(rows, i):
                y = c
            elif x is None and not _is_numeric_column(rows, i):
                x = c
        # Fallbacks if everything was numeric or everything was text
        if x is None and len(columns) > 0:
            x = columns[0]
        if y is None and len(columns) > 1:
            y = columns[1]
        elif y is None and columns:
            y = columns[0]

    return x, y


# ---------------------------------------------------------------------------
# Rendering paths
# ---------------------------------------------------------------------------

def _render_kpi(visual: PlannedVisual, result: QueryResult) -> RenderedVisual:
    """Single big number — pulls visual.y_col (or first numeric col) from row 0."""
    raw_value: Any = None
    if result.rows:
        # Prefer the planner's y_col, else the first numeric column
        cols = result.columns
        target = visual.y_col if (visual.y_col and visual.y_col in cols) else None
        if target is None:
            for i, c in enumerate(cols):
                if _is_numeric_column(result.rows, i):
                    target = c
                    break
        if target is None and cols:
            target = cols[0]
        if target:
            raw_value = result.rows[0][cols.index(target)]

    return RenderedVisual(
        visual_id=visual.id,
        type="kpi",
        title=visual.title,
        subtitle=visual.subtitle,
        from_query=visual.from_query,
        sql=result.sql,
        rows_count=result.rows_count,
        kpi=KPIPayload(
            label=visual.title,
            value=raw_value,
            formatted_value=_format_number(raw_value, visual.unit),
            unit=visual.unit,
        ),
    )


def _render_table(visual: PlannedVisual, result: QueryResult) -> RenderedVisual:
    return RenderedVisual(
        visual_id=visual.id,
        type="table",
        title=visual.title,
        subtitle=visual.subtitle,
        from_query=visual.from_query,
        sql=result.sql,
        rows_count=result.rows_count,
        table_columns=result.columns,
        table_rows=result.rows[:200],   # tables in dashboards stay readable
    )


def _render_chart(visual: PlannedVisual, result: QueryResult) -> RenderedVisual:
    """Build an ECharts option using the existing viz_service builder."""
    cols = result.columns
    rows = result.rows

    x, y = _pick_x_y(cols, rows, visual.x_col, visual.y_col)

    spec = ChartSpec(
        chart_type=visual.type,
        title=visual.title,
        x=x or "",
        y=y or "",
        color=visual.color_col or "",
    )

    # Cap points for readability — bar/pie/donut especially benefit
    if visual.type in ("bar", "horizontal_bar", "pie", "donut", "funnel", "treemap"):
        spec.limit = min(20, len(rows))

    option = build_echarts_option(spec, rows, cols)

    return RenderedVisual(
        visual_id=visual.id,
        type=visual.type,
        title=visual.title,
        subtitle=visual.subtitle,
        from_query=visual.from_query,
        sql=result.sql,
        rows_count=result.rows_count,
        echarts_option=option,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def design_visual(visual: PlannedVisual, result: QueryResult) -> RenderedVisual:
    """Convert one PlannedVisual + its query result into a RenderedVisual."""
    if not result.success:
        return RenderedVisual(
            visual_id=visual.id,
            type=visual.type,
            title=visual.title,
            subtitle=visual.subtitle,
            from_query=visual.from_query,
            sql=result.sql,
            rows_count=0,
            error=result.error or "Source query failed.",
        )

    if not result.rows:
        return RenderedVisual(
            visual_id=visual.id,
            type=visual.type,
            title=visual.title,
            subtitle=visual.subtitle,
            from_query=visual.from_query,
            sql=result.sql,
            rows_count=0,
            error="Query returned no rows.",
        )

    if visual.type == "kpi":
        return _render_kpi(visual, result)
    if visual.type == "table":
        return _render_table(visual, result)
    return _render_chart(visual, result)


def design_all_visuals(
    visuals: list[PlannedVisual],
    results: dict[str, QueryResult],
) -> list[RenderedVisual]:
    """Render every visual in the plan; skips visuals whose query is missing."""
    out: list[RenderedVisual] = []
    for v in visuals:
        result = results.get(v.from_query)
        if result is None:
            out.append(RenderedVisual(
                visual_id=v.id, type=v.type, title=v.title,
                from_query=v.from_query,
                error=f"No query result available for {v.from_query!r}.",
            ))
            continue
        out.append(design_visual(v, result))
    return out
