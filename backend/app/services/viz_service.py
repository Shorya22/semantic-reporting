"""
Local chart and dashboard rendering — Plotly + Kaleido, no cloud calls.

Supported chart types
---------------------
bar, horizontal_bar, line, area, scatter, pie, donut,
histogram, heatmap, treemap, funnel, box, violin,
bubble, waterfall, gauge, indicator

Dashboard
---------
``render_dashboard`` renders each panel individually then composes them
into a professional grid image using Plotly layout-image annotations.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_TEMPLATE   = "plotly_dark"
_BG         = "#0f1629"
_PAPER_BG   = "#0a0f1e"
_FONT_COLOR = "#e2e8f0"
_GRID_COLOR = "#1e2a45"

_COLORS = [
    "#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#f43f5e",
    "#a78bfa", "#34d399", "#fb923c", "#38bdf8", "#e879f9",
    "#84cc16", "#f97316",
]

_BASE_LAYOUT: dict[str, Any] = dict(
    template=_TEMPLATE,
    plot_bgcolor=_BG,
    paper_bgcolor=_PAPER_BG,
    font=dict(color=_FONT_COLOR, family="Inter, system-ui, sans-serif", size=12),
    legend=dict(bgcolor="rgba(0,0,0,0.4)", bordercolor=_GRID_COLOR, borderwidth=1),
    xaxis=dict(gridcolor=_GRID_COLOR, linecolor=_GRID_COLOR, zerolinecolor=_GRID_COLOR),
    yaxis=dict(gridcolor=_GRID_COLOR, linecolor=_GRID_COLOR, zerolinecolor=_GRID_COLOR),
    margin=dict(l=60, r=40, t=60, b=60),
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ChartSpec:
    chart_type: str = "bar"
    title: str = ""
    x: str = ""
    y: str = ""
    color: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    aggregation: str = ""        # sum | count | avg | max | min
    sort: str = ""               # asc | desc
    limit: int = 0               # 0 = no limit
    extra: dict[str, Any] = field(default_factory=dict)


def spec_from_dict(d: dict) -> ChartSpec:
    """Build a ChartSpec from a dict (parsed from LLM <chart> output)."""
    _skip = {
        "chart_type", "type", "title",
        "x", "x_col", "x_axis",
        "y", "y_col", "y_axis",
        "color", "color_col", "color_by",
        "labels", "aggregation", "agg", "sort", "limit",
    }
    return ChartSpec(
        chart_type  = d.get("chart_type", d.get("type", "bar")),
        title       = d.get("title", ""),
        x           = d.get("x", d.get("x_col", d.get("x_axis", ""))),
        y           = d.get("y", d.get("y_col", d.get("y_axis", ""))),
        color       = d.get("color", d.get("color_col", d.get("color_by", ""))),
        labels      = d.get("labels", {}),
        aggregation = d.get("aggregation", d.get("agg", "")),
        sort        = d.get("sort", ""),
        limit       = int(d.get("limit", 0)),
        extra       = {k: v for k, v in d.items() if k not in _skip},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(rows: list, columns: list[str]) -> pd.DataFrame:
    if not rows or not columns:
        return pd.DataFrame()
    if isinstance(rows[0], dict):
        return pd.DataFrame(rows)
    return pd.DataFrame(rows, columns=columns)


def _col(df: pd.DataFrame, hint: str, fallback_idx: int = 0) -> str:
    """Return hint if it's a real column, else df.columns[fallback_idx]."""
    if hint and hint in df.columns:
        return hint
    return df.columns[fallback_idx] if len(df.columns) > fallback_idx else df.columns[0]


_ISO_DATE_RE = __import__("re").compile(
    r"^\d{4}-\d{2}-\d{2}"             # YYYY-MM-DD
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?"  # optional THH:MM[:SS]
    r"(?:\.\d+)?"                     # optional microseconds
    r"(?:Z|[+-]\d{2}:?\d{2})?"        # optional timezone
    r")?$"
)


def _format_date_labels(x_vals: list) -> list:
    """If every x-value looks like an ISO date/datetime, return a list of
    short, human-readable labels. Otherwise return ``x_vals`` unchanged.

    Heuristics:
      * all values are first-of-month at 00:00 → "Jan 2026" (or just "Jan"
        when every value falls in the same year — saves horizontal space
        on narrow charts)
      * all values are within one year         → "15 Jan"
      * else                                   → "15 Jan 2026"

    The original strings already render correctly when the chart uses a
    time axis; this helper is for the common case where the SQL agent
    produces a categorical (string) axis from ``strftime`` / Postgres
    ``to_char`` casts and the labels arrive as raw ISO timestamps.
    """
    from datetime import datetime

    if not x_vals:
        return x_vals

    parsed: list[datetime] = []
    for v in x_vals:
        s = str(v).strip()
        if not _ISO_DATE_RE.match(s):
            return x_vals
        try:
            # ``fromisoformat`` accepts the trailing "Z" only on 3.11+,
            # so normalise to "+00:00" first for safety.
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return x_vals
        # Drop timezone info WITHOUT converting — the agent's SQL produced
        # "2026-01-01 00:00:00+05:30" because the source DB stores wall-clock
        # local time. Converting to UTC would silently shift "Jan 1" back
        # to "Dec 31"; we only care about the displayed calendar position.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        parsed.append(dt)

    if len(parsed) < 2:
        # One point — keep as-is (still readable).
        return x_vals

    all_first_of_month = all(
        dt.day == 1 and dt.hour == 0 and dt.minute == 0 and dt.second == 0
        for dt in parsed
    )
    same_year = len({dt.year for dt in parsed}) == 1
    span_days = (max(parsed) - min(parsed)).days

    if all_first_of_month:
        if same_year:
            return [dt.strftime("%b") for dt in parsed]            # "Jan"
        return [dt.strftime("%b %Y") for dt in parsed]              # "Jan 2026"

    if span_days < 365:
        return [dt.strftime("%d %b") for dt in parsed]              # "15 Jan"
    return [dt.strftime("%d %b %Y") for dt in parsed]               # "15 Jan 2026"


def _grid_bottom(x_vals: list) -> str:
    """Return a ``grid.bottom`` percentage that accommodates rotated x-labels.

    Charts where the longest category name exceeds 8 characters get rotated
    30°; this helper grows the bottom margin in proportion to the label
    length so the rotated text never clips into the bottom edge of the
    chart card (a recurring complaint in the dashboard view).

    Tuned empirically:
      * label length ≤ 8       →  12% (original default, no rotation)
      * label length 9–14      →  18%
      * label length 15–22     →  24%
      * label length ≥ 23      →  30%
    """
    if not x_vals:
        return "12%"
    try:
        longest = max(len(str(v)) for v in x_vals)
    except ValueError:
        return "12%"
    if longest <= 8:
        return "12%"
    if longest <= 14:
        return "18%"
    if longest <= 22:
        return "24%"
    return "30%"


def _pivot_series(
    df: pd.DataFrame,
    x_col: str,
    series_col: str,
    y_col: str,
) -> tuple[list[str], dict[str, list[float]]]:
    """Pivot ``df`` for a multi-series chart.

    Used by stacked_bar, grouped_bar, stacked_area, multi_line, and any
    other chart that wants ``color_col`` as the series dimension.

    Returns ``(x_axis_values, {series_label: aligned_y_values})``. Aligned
    means each series list has the same length as ``x_axis_values`` —
    missing combinations get a 0. Order of x-values is preserved as the
    first occurrence in ``df``; series order is alphabetical for stable
    legends.
    """
    if not (x_col and series_col and y_col):
        return [], {}
    if not all(c in df.columns for c in (x_col, series_col, y_col)):
        return [], {}
    try:
        pivoted = (
            df.pivot_table(
                index=x_col, columns=series_col, values=y_col,
                aggfunc="sum", fill_value=0,
            )
        )
    except Exception:
        return [], {}
    x_axis_vals = [str(v) for v in pivoted.index.tolist()]
    series_map: dict[str, list[float]] = {}
    for col in sorted(pivoted.columns, key=lambda v: str(v)):
        series_map[str(col)] = [float(v) for v in pivoted[col].tolist()]
    return x_axis_vals, series_map


def _apply_agg(df: pd.DataFrame, spec: ChartSpec) -> pd.DataFrame:
    if spec.aggregation and spec.x and spec.y:
        agg_map = {
            "sum": "sum", "count": "count",
            "avg": "mean", "average": "mean",
            "max": "max",  "min":    "min",
        }
        agg_fn = agg_map.get(spec.aggregation.lower(), "sum")
        group_cols = [c for c in [spec.x, spec.color] if c and c in df.columns]
        if group_cols and spec.y in df.columns:
            df = df.groupby(group_cols, as_index=False).agg({spec.y: agg_fn})

    if spec.sort and spec.y and spec.y in df.columns:
        df = df.sort_values(spec.y, ascending=spec.sort.lower() != "desc")

    if spec.limit and spec.limit > 0:
        df = df.head(spec.limit)

    return df


def _kwargs(spec: ChartSpec) -> dict[str, Any]:
    kw: dict[str, Any] = {"color_discrete_sequence": _COLORS, "template": _TEMPLATE}
    if spec.title:
        kw["title"] = spec.title
    if spec.labels:
        kw["labels"] = spec.labels
    if spec.color and spec.color:   # column presence checked at call site
        kw["color"] = spec.color
    return kw


def _apply_base_layout(fig: go.Figure, ct: str) -> go.Figure:
    update = dict(_BASE_LAYOUT)
    if ct in ("pie", "donut", "treemap", "indicator", "gauge"):
        update.pop("xaxis", None)
        update.pop("yaxis", None)
    # template key conflicts with plotly internals when passed via update_layout
    update.pop("template", None)
    fig.update_layout(**update)
    return fig


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------

def _make_figure(df: pd.DataFrame, spec: ChartSpec) -> go.Figure:
    ct = spec.chart_type.lower().replace("-", "_").replace(" ", "_")
    kw = _kwargs(spec)
    if spec.color and spec.color not in df.columns:
        kw.pop("color", None)

    x_col = _col(df, spec.x, 0)
    y_col = _col(df, spec.y, 1 if len(df.columns) > 1 else 0)

    if ct in ("bar", "column"):
        fig = px.bar(df, x=x_col, y=y_col, **kw)

    elif ct == "horizontal_bar":
        fig = px.bar(df, x=y_col, y=x_col, orientation="h", **kw)

    elif ct == "line":
        fig = px.line(df, x=x_col, y=y_col, markers=True, **kw)

    elif ct == "area":
        fig = px.area(df, x=x_col, y=y_col, **kw)

    elif ct == "scatter":
        fig = px.scatter(df, x=x_col, y=y_col, **kw)

    elif ct in ("pie", "donut"):
        hole = 0.42 if ct == "donut" else 0
        pie_kw = {k: v for k, v in kw.items() if k != "color"}
        fig = px.pie(df, names=x_col, values=y_col, hole=hole, **pie_kw)

    elif ct == "histogram":
        fig = px.histogram(df, x=x_col, **kw)

    elif ct == "heatmap":
        z_col = spec.extra.get("z") or (df.columns[2] if len(df.columns) > 2 else y_col)
        if z_col in df.columns and x_col in df.columns and y_col in df.columns:
            pivot = df.pivot_table(index=y_col, columns=x_col, values=z_col, aggfunc="sum").fillna(0)
            fig = go.Figure(go.Heatmap(
                z=pivot.values.tolist(),
                x=list(pivot.columns),
                y=list(pivot.index),
                colorscale="Viridis",
            ))
            if spec.title:
                fig.update_layout(title=spec.title)
        else:
            numeric = df.select_dtypes(include="number")
            fig = px.imshow(numeric, **{k: v for k, v in kw.items() if k in ("title", "template")})

    elif ct == "treemap":
        path = [x_col]
        tree_kw = {k: v for k, v in kw.items() if k in ("title", "template", "color_discrete_sequence")}
        fig = px.treemap(df, path=path, values=y_col, **tree_kw)

    elif ct == "funnel":
        fig = px.funnel(df, x=y_col, y=x_col, **kw)

    elif ct in ("box", "boxplot"):
        fig = px.box(df, x=x_col, y=y_col, **kw)

    elif ct == "violin":
        fig = px.violin(df, x=x_col, y=y_col, **kw)

    elif ct == "bubble":
        sz = spec.extra.get("size")
        size_col = sz if sz and sz in df.columns else None
        z_col_3 = df.columns[2] if not size_col and len(df.columns) > 2 else size_col
        fig = px.scatter(df, x=x_col, y=y_col,
                         size=z_col_3 if z_col_3 and z_col_3 in df.columns else None, **kw)

    elif ct == "waterfall":
        x_vals = df[x_col].tolist()
        y_vals = df[y_col].tolist() if y_col in df.columns else []
        measure = spec.extra.get("measure", ["relative"] * len(x_vals))
        if isinstance(measure, str):
            measure = [measure] * len(x_vals)
        fig = go.Figure(go.Waterfall(
            x=x_vals, y=y_vals,
            measure=measure[:len(x_vals)],
            connector={"line": {"color": "#6366f1"}},
            increasing={"marker": {"color": "#10b981"}},
            decreasing={"marker": {"color": "#f43f5e"}},
        ))
        if spec.title:
            fig.update_layout(title=spec.title)

    elif ct in ("gauge", "indicator"):
        value = float(df[y_col].iloc[0]) if y_col in df.columns and not df.empty else 0
        mode = "number+gauge" if ct == "gauge" else "number+delta"
        gauge_cfg = {"axis": {"visible": True}, "bar": {"color": "#6366f1"}} if ct == "gauge" else {}
        fig = go.Figure(go.Indicator(
            mode=mode,
            value=value,
            title={"text": spec.title or y_col},
            gauge=gauge_cfg,
        ))

    else:
        fig = px.bar(df, x=x_col, y=y_col, **kw)

    return _apply_base_layout(fig, ct)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_chart(
    spec: ChartSpec,
    rows: list,
    columns: list[str],
    width: int = 900,
    height: int = 500,
) -> str:
    """
    Render a single chart → base64 PNG string.

    Parameters
    ----------
    spec:    Chart specification.
    rows:    Raw SQL result rows (list of lists or list of dicts).
    columns: Column names.
    width:   Image width in pixels.
    height:  Image height in pixels.

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    df = _df(rows, columns)
    if df.empty:
        return _empty_png(spec.title or "No data available", width, height)

    df = _apply_agg(df, spec)
    if df.empty:
        return _empty_png(spec.title or "No data after aggregation", width, height)

    fig = _make_figure(df, spec)
    fig.update_layout(width=width, height=height)
    png = fig.to_image(format="png", width=width, height=height, scale=2)
    return base64.b64encode(png).decode()


def render_dashboard(
    panels: list[tuple[ChartSpec, list, list[str]]],
    title: str = "Dashboard",
    cols: int = 2,
    panel_width: int = 800,
    panel_height: int = 420,
) -> str:
    """
    Render multiple charts as a professional grid dashboard.

    Each panel is rendered individually then composited using Plotly layout
    image annotations — this handles all chart types uniformly.

    Parameters
    ----------
    panels:       List of (ChartSpec, rows, columns) tuples.
    title:        Dashboard title.
    cols:         Grid columns.
    panel_width:  Width per panel (px).
    panel_height: Height per panel (px).

    Returns
    -------
    str
        Base64-encoded PNG of the full dashboard.
    """
    if not panels:
        return _empty_png("No data", panel_width, panel_height)

    rows_count  = (len(panels) + cols - 1) // cols
    title_px    = 72
    total_w     = panel_width * cols
    total_h     = panel_height * rows_count + title_px

    # Render each panel as its own PNG
    panel_b64s = [
        render_chart(spec, rows, columns, panel_width, panel_height)
        for spec, rows, columns in panels
    ]

    # Compose via Plotly layout images
    images = []
    chart_area = 1 - (title_px / total_h)
    for idx, b64 in enumerate(panel_b64s):
        row = idx // cols
        col = idx % cols
        x0 = col / cols
        x1 = (col + 1) / cols
        y1 = chart_area * (1 - row / rows_count)
        y0 = chart_area * (1 - (row + 1) / rows_count)
        images.append(dict(
            source=f"data:image/png;base64,{b64}",
            xref="paper", yref="paper",
            x=x0, y=y1,
            sizex=x1 - x0,
            sizey=y1 - y0,
            sizing="stretch",
            opacity=1.0,
            layer="above",
        ))

    fig = go.Figure()
    fig.update_layout(
        title=dict(text=title, font=dict(size=22, color=_FONT_COLOR, family="Inter, system-ui"), x=0.5),
        paper_bgcolor=_PAPER_BG,
        plot_bgcolor=_PAPER_BG,
        width=total_w,
        height=total_h,
        images=images,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(l=0, r=0, t=title_px, b=0),
    )
    png = fig.to_image(format="png", width=total_w, height=total_h, scale=2)
    return base64.b64encode(png).decode()


def _empty_png(message: str, width: int, height: int) -> str:
    """Return a base64 PNG showing a centered message on dark background."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, x=0.5, y=0.5,
        xref="paper", yref="paper",
        showarrow=False,
        font=dict(size=16, color="#94a3b8"),
    )
    fig.update_layout(
        paper_bgcolor=_PAPER_BG, plot_bgcolor=_BG,
        width=width, height=height,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        margin=dict(l=0, r=0, t=0, b=0),
    )
    png = fig.to_image(format="png", width=width, height=height, scale=2)
    return base64.b64encode(png).decode()


# ---------------------------------------------------------------------------
# ECharts JSON option builder (client-side interactive rendering)
# ---------------------------------------------------------------------------

def build_echarts_option(spec: "ChartSpec", rows: list, columns: list) -> dict:
    """
    Build an Apache ECharts JSON option object from query results.
    Returned dict is JSON-serializable and passed directly to the frontend ECharts instance.
    """
    import pandas as pd

    COLORS = [
        "#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#f43f5e",
        "#a78bfa", "#34d399", "#fb923c", "#38bdf8", "#e879f9",
        "#84cc16", "#f97316",
    ]

    df = pd.DataFrame(rows, columns=columns)

    # Sort
    if spec.sort and spec.y and spec.y in df.columns:
        ascending = spec.sort == "asc"
        df = df.sort_values(spec.y, ascending=ascending)

    # Limit
    if spec.limit and spec.limit > 0:
        df = df.head(spec.limit)

    x_vals: list = df[spec.x].astype(str).tolist() if spec.x and spec.x in df.columns else []
    y_vals: list = df[spec.y].tolist() if spec.y and spec.y in df.columns else []

    # Auto-format ISO date strings into short human labels — fixes the
    # "2026-01-01 00:00:00+05:30" axis-clutter problem on time-series
    # charts. No-op when the values aren't dates.
    x_vals = _format_date_labels(x_vals)

    # Convert numpy/pandas types to Python native for JSON serialization
    def _native(v: Any) -> Any:
        try:
            return float(v) if hasattr(v, "__float__") else v
        except Exception:
            return str(v)

    y_vals = [_native(v) for v in y_vals]

    base: dict = {
        "backgroundColor": "#0a0f1e",
        "title": {
            "text": spec.title or "",
            "textStyle": {
                "color": "#e2e8f0",
                "fontSize": 14,
                "fontWeight": "600",
                "fontFamily": "Inter, system-ui, sans-serif",
            },
            "left": "center",
            "top": 8,
        },
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "#1e2a45",
            "borderColor": "#334155",
            "borderWidth": 1,
            "textStyle": {"color": "#e2e8f0", "fontSize": 12},
            "axisPointer": {
                "type": "shadow",
                "shadowStyle": {"color": "rgba(99,102,241,0.08)"},
            },
        },
        # Default grid; per-chart-type branches below tweak ``bottom`` based
        # on the longest x-label (so rotated labels always fit on screen
        # without clipping into the chart card edge).
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": _grid_bottom(x_vals),
            "top": "18%",
            "containLabel": True,
        },
        "toolbox": {
            "right": 12,
            "top": 4,
            "feature": {
                "saveAsImage": {
                    "title": "Save",
                    "pixelRatio": 2,
                    "iconStyle": {"borderColor": "#6366f1"},
                },
                "dataZoom": {
                    "title": {"zoom": "Zoom", "back": "Reset"},
                    "iconStyle": {"borderColor": "#6366f1"},
                },
                "restore": {
                    "title": "Reset",
                    "iconStyle": {"borderColor": "#6366f1"},
                },
            },
        },
    }

    ct = spec.chart_type

    # ── Axis helpers ──────────────────────────────────────────────────────────
    def _x_axis(rotate: int = 0) -> dict:
        return {
            "type": "category",
            "data": x_vals,
            "axisLine": {"lineStyle": {"color": "#334155"}},
            "axisTick": {"lineStyle": {"color": "#334155"}},
            "axisLabel": {"color": "#94a3b8", "rotate": rotate, "fontSize": 11},
            "splitLine": {"show": False},
        }

    def _y_axis() -> dict:
        return {
            "type": "value",
            "axisLine": {"lineStyle": {"color": "#334155"}},
            "axisLabel": {"color": "#94a3b8", "fontSize": 11},
            "splitLine": {"lineStyle": {"color": "#1e2a45", "type": "dashed"}},
        }

    # ── Gradient helpers ──────────────────────────────────────────────────────
    def _v_gradient(top: str, bot: str) -> dict:
        return {
            "type": "linear",
            "x": 0, "y": 0, "x2": 0, "y2": 1,
            "colorStops": [
                {"offset": 0, "color": top},
                {"offset": 1, "color": bot},
            ],
        }

    def _h_gradient(left: str, right: str) -> dict:
        return {
            "type": "linear",
            "x": 0, "y": 0, "x2": 1, "y2": 0,
            "colorStops": [
                {"offset": 0, "color": left},
                {"offset": 1, "color": right},
            ],
        }

    # ── Chart type builders ───────────────────────────────────────────────────

    if ct == "bar":
        rotate = 30 if x_vals and max((len(str(v)) for v in x_vals), default=0) > 8 else 0
        base.update({
            "xAxis": _x_axis(rotate),
            "yAxis": _y_axis(),
            "series": [{
                "type": "bar",
                "data": y_vals,
                "barMaxWidth": 60,
                "itemStyle": {
                    "color": _v_gradient("#6366f1", "#4338ca"),
                    "borderRadius": [4, 4, 0, 0],
                },
                "emphasis": {"itemStyle": {"color": _v_gradient("#818cf8", "#6366f1")}},
                "label": {
                    "show": len(y_vals) <= 10,
                    "position": "top",
                    "color": "#94a3b8",
                    "fontSize": 11,
                },
            }],
        })

    elif ct == "horizontal_bar":
        base["tooltip"]["axisPointer"] = {"type": "shadow"}
        base.pop("grid", None)
        base["grid"] = {
            "left": "3%", "right": "8%",
            "bottom": "5%", "top": "15%",
            "containLabel": True,
        }
        base.update({
            "xAxis": {
                "type": "value",
                **{k: v for k, v in _y_axis().items() if k != "type"},
            },
            "yAxis": {
                "type": "category",
                "data": x_vals,
                "axisLabel": {"color": "#94a3b8", "fontSize": 11},
                "axisLine": {"lineStyle": {"color": "#334155"}},
            },
            "series": [{
                "type": "bar",
                "data": y_vals,
                "barMaxWidth": 40,
                "itemStyle": {
                    "color": _h_gradient("#6366f1", "#22d3ee"),
                    "borderRadius": [0, 4, 4, 0],
                },
                "emphasis": {"itemStyle": {"color": _h_gradient("#818cf8", "#38bdf8")}},
                "label": {
                    "show": True,
                    "position": "right",
                    "color": "#94a3b8",
                    "fontSize": 11,
                },
            }],
        })

    elif ct in ("line", "area"):
        series_entry: dict = {
            "type": "line",
            "data": y_vals,
            "smooth": True,
            "symbol": "circle",
            "symbolSize": 6,
            "lineStyle": {"color": "#6366f1", "width": 2.5},
            "itemStyle": {
                "color": "#6366f1",
                "borderColor": "#0a0f1e",
                "borderWidth": 2,
            },
            "emphasis": {"itemStyle": {"scale": 1.5}},
        }
        if ct == "area":
            series_entry["areaStyle"] = {
                "color": _v_gradient(
                    "rgba(99,102,241,0.35)",
                    "rgba(99,102,241,0.02)",
                )
            }
        base.update({
            "xAxis": {**_x_axis(), "boundaryGap": False},
            "yAxis": _y_axis(),
            "series": [series_entry],
        })

    elif ct in ("pie", "donut"):
        base.pop("grid", None)
        base.pop("toolbox", None)
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = "{b}: {c} ({d}%)"
        pie_data = [
            {
                "name": str(x),
                "value": y,
                "itemStyle": {"color": COLORS[i % len(COLORS)]},
            }
            for i, (x, y) in enumerate(zip(x_vals, y_vals))
        ]
        radius = ["45%", "72%"] if ct == "donut" else ["0%", "68%"]
        base["series"] = [{
            "type": "pie",
            "radius": radius,
            "center": ["50%", "55%"],
            "data": pie_data,
            "itemStyle": {
                "borderColor": "#0a0f1e",
                "borderWidth": 2,
                "borderRadius": 4,
            },
            # Avoid the "Education / Insurance / PDS stacked at top" issue
            # by letting ECharts hide labels that would collide and giving
            # the leader lines enough room to spread the remaining labels
            # cleanly along the chart edge.
            "avoidLabelOverlap": True,
            "labelLayout": {"hideOverlap": True, "moveOverlap": "shiftY"},
            "label": {
                "color":     "#e2e8f0",
                "fontSize":  11,
                "alignTo":   "edgeWidth",
                "edgeDistance": "8%",
            },
            "labelLine": {
                "length":  14,
                "length2": 8,
                "maxSurfaceAngle": 80,
                "lineStyle": {"color": "#475569"},
            },
            "emphasis": {
                "itemStyle": {"shadowBlur": 15, "shadowColor": "rgba(0,0,0,0.5)"},
            },
        }]

    elif ct == "scatter":
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = f"{{b}}<br/>{spec.x}: {{c[0]}}<br/>{spec.y}: {{c[1]}}"
        scatter_data: list = []
        if spec.x and spec.x in df.columns and spec.y and spec.y in df.columns:
            for row in df.itertuples(index=False):
                scatter_data.append([
                    _native(getattr(row, spec.x, 0)),
                    _native(getattr(row, spec.y, 0)),
                ])
        base.update({
            "xAxis": {
                "type": "value",
                "name": spec.x or "X",
                "nameTextStyle": {"color": "#94a3b8"},
                "axisLabel": {"color": "#94a3b8"},
                "splitLine": {"lineStyle": {"color": "#1e2a45", "type": "dashed"}},
            },
            "yAxis": {
                "type": "value",
                "name": spec.y or "Y",
                "nameTextStyle": {"color": "#94a3b8"},
                "axisLabel": {"color": "#94a3b8"},
                "splitLine": {"lineStyle": {"color": "#1e2a45", "type": "dashed"}},
            },
            "series": [{
                "type": "scatter",
                "data": scatter_data,
                "itemStyle": {"color": "#6366f1", "opacity": 0.75},
                "emphasis": {
                    "itemStyle": {"opacity": 1, "shadowBlur": 10, "shadowColor": "#6366f1"},
                },
            }],
        })

    elif ct == "funnel":
        base.pop("grid", None)
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = "{a} <br/>{b} : {c} ({d}%)"
        funnel_data = [
            {
                "name": str(x),
                "value": y,
                "itemStyle": {"color": COLORS[i % len(COLORS)]},
            }
            for i, (x, y) in enumerate(zip(x_vals, y_vals))
        ]
        base["series"] = [{
            "type": "funnel",
            "left": "10%",
            "width": "80%",
            "data": sorted(funnel_data, key=lambda d: d["value"], reverse=True),
            "label": {"position": "inside", "color": "#fff", "fontSize": 12},
            "itemStyle": {"borderColor": "#0a0f1e", "borderWidth": 2},
        }]

    elif ct == "treemap":
        base.pop("grid", None)
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = "{b}: {c}"
        treemap_data = [
            {
                "name": str(x),
                "value": y,
                "itemStyle": {"color": COLORS[i % len(COLORS)]},
            }
            for i, (x, y) in enumerate(zip(x_vals, y_vals))
        ]
        base["series"] = [{
            "type": "treemap",
            "roam": False,
            "data": treemap_data,
            "label": {"show": True, "color": "#e2e8f0", "fontSize": 12},
            "breadcrumb": {"show": False},
            "itemStyle": {"borderColor": "#0a0f1e", "borderWidth": 2},
        }]

    elif ct == "gauge":
        base.pop("grid", None)
        val = y_vals[0] if y_vals else 0
        base["series"] = [{
            "type": "gauge",
            "progress": {
                "show": True,
                "width": 14,
                "itemStyle": {"color": "#6366f1"},
            },
            "axisLine": {
                "lineStyle": {"width": 14, "color": [[1, "#1e2a45"]]},
            },
            "axisTick": {"show": False},
            "splitLine": {"length": 12, "lineStyle": {"color": "#334155"}},
            "axisLabel": {"color": "#94a3b8"},
            "pointer": {"itemStyle": {"color": "#6366f1"}},
            "detail": {
                "valueAnimation": True,
                "color": "#e2e8f0",
                "fontSize": 20,
                "fontWeight": "bold",
            },
            "data": [{"value": round(float(val), 2), "name": spec.y or spec.title or "Value"}],
        }]

    elif ct == "histogram":
        if spec.x and spec.x in df.columns:
            try:
                hist_vals = df[spec.x].dropna().astype(float).tolist()
            except (ValueError, TypeError):
                hist_vals = []
            bins = 10
            if hist_vals:
                mn, mx = min(hist_vals), max(hist_vals)
                width = (mx - mn) / bins if mx != mn else 1
                counts = [0] * bins
                for v in hist_vals:
                    idx = min(int((v - mn) / width), bins - 1)
                    counts[idx] += 1
                labels = [f"{mn + i * width:.1f}" for i in range(bins)]
                base.update({
                    "xAxis": {
                        "type": "category",
                        "data": labels,
                        "axisLabel": {"color": "#94a3b8", "fontSize": 10},
                    },
                    "yAxis": _y_axis(),
                    "series": [{
                        "type": "bar",
                        "data": counts,
                        "itemStyle": {"color": _v_gradient("#6366f1", "#4338ca")},
                        "barCategoryGap": "5%",
                    }],
                })

    elif ct == "box":
        # Compute proper [min, q1, median, q3, max] per group when the
        # data has a numeric y_col and a categorical x_col.
        boxplot_data: list = []
        try:
            if spec.x and spec.x in df.columns and spec.y and spec.y in df.columns:
                grouped = df.groupby(spec.x)[spec.y]
                for _grp, vals in grouped:
                    series = pd.to_numeric(vals, errors="coerce").dropna()
                    if series.empty:
                        continue
                    quartiles = series.quantile([0.0, 0.25, 0.5, 0.75, 1.0]).tolist()
                    boxplot_data.append([float(v) for v in quartiles])
        except Exception:
            boxplot_data = []
        base.update({
            "xAxis": _x_axis(),
            "yAxis": _y_axis(),
            "series": [{
                "type": "boxplot",
                "data": boxplot_data,
                "itemStyle": {"color": "#6366f1", "borderColor": "#818cf8"},
            }],
        })

    # ── Multi-series pivots (use color_col as the series dimension) ─────────

    elif ct in ("stacked_bar", "stacked_horizontal_bar", "grouped_bar",
                "stacked_area", "multi_line"):
        x_axis_vals, series_map = _pivot_series(df, spec.x, spec.color, spec.y)
        if not series_map:
            # No multi-series data — degrade to a plain single-series chart.
            base.update({
                "xAxis": _x_axis(),
                "yAxis": _y_axis(),
                "series": [{
                    "type": "bar" if "bar" in ct else "line",
                    "data": y_vals,
                    "itemStyle": {"color": _v_gradient("#6366f1", "#4338ca")},
                }],
            })
        else:
            horizontal = ct == "stacked_horizontal_bar"
            stack_key  = "total" if ct in ("stacked_bar", "stacked_horizontal_bar", "stacked_area") else None
            series_type = "line" if ct in ("multi_line", "stacked_area") else "bar"
            is_area     = ct == "stacked_area"

            if horizontal:
                base.pop("grid", None)
                base["grid"] = {"left": "3%", "right": "8%", "bottom": "5%", "top": "18%", "containLabel": True}
                base["xAxis"] = {"type": "value", **{k: v for k, v in _y_axis().items() if k != "type"}}
                base["yAxis"] = {
                    "type": "category", "data": x_axis_vals,
                    "axisLabel": {"color": "#94a3b8", "fontSize": 11},
                    "axisLine": {"lineStyle": {"color": "#334155"}},
                }
            else:
                base["xAxis"] = {**_x_axis(rotate=30 if max((len(s) for s in x_axis_vals), default=0) > 8 else 0),
                                 "boundaryGap": series_type == "bar"}
                base["yAxis"] = _y_axis()

            base["legend"] = {
                "data": list(series_map.keys()),
                "textStyle": {"color": "#cbd5e1", "fontSize": 11},
                "top": 32, "type": "scroll",
            }

            new_series = []
            for i, (name, values) in enumerate(series_map.items()):
                color = COLORS[i % len(COLORS)]
                entry: dict[str, Any] = {
                    "name": name,
                    "type": series_type,
                    "data": values,
                    "itemStyle": {"color": color},
                }
                if stack_key:
                    entry["stack"] = stack_key
                if series_type == "line":
                    entry["smooth"] = True
                    entry["symbol"] = "circle"
                    entry["symbolSize"] = 5
                    entry["lineStyle"] = {"width": 2.2}
                    if is_area:
                        entry["areaStyle"] = {"opacity": 0.45}
                else:
                    entry["barMaxWidth"] = 60
                    entry["itemStyle"] = {"color": color, "borderRadius": [3, 3, 0, 0] if not horizontal else [0, 3, 3, 0]}
                new_series.append(entry)

            base["series"] = new_series
            base["tooltip"]["trigger"] = "axis"

    # ── Heatmap (2D matrix) ─────────────────────────────────────────────────

    elif ct == "heatmap":
        # Expect 3 columns: x (category), y (category), z (numeric).
        # Falls back to bar if shape doesn't match.
        x_col = spec.x or (columns[0] if len(columns) > 0 else "")
        y_col = spec.color or (columns[1] if len(columns) > 1 else "")
        z_col = spec.y or (columns[2] if len(columns) > 2 else "")
        if not (x_col in df.columns and y_col in df.columns and z_col in df.columns):
            base.update({"xAxis": _x_axis(), "yAxis": _y_axis(),
                         "series": [{"type": "bar", "data": y_vals,
                                     "itemStyle": {"color": _v_gradient("#6366f1", "#4338ca")}}]})
        else:
            x_uniq = list(dict.fromkeys(df[x_col].astype(str).tolist()))
            y_uniq = list(dict.fromkeys(df[y_col].astype(str).tolist()))
            xi = {v: i for i, v in enumerate(x_uniq)}
            yi = {v: i for i, v in enumerate(y_uniq)}
            data: list[list[Any]] = []
            zvals: list[float] = []
            for _row in df.itertuples(index=False):
                xv = str(getattr(_row, x_col))
                yv = str(getattr(_row, y_col))
                try:
                    zv = float(getattr(_row, z_col))
                except (TypeError, ValueError):
                    continue
                data.append([xi[xv], yi[yv], zv])
                zvals.append(zv)
            zmin = min(zvals) if zvals else 0.0
            zmax = max(zvals) if zvals else 1.0
            base.pop("toolbox", None)
            base["tooltip"] = {
                "position": "top",
                "backgroundColor": "#1e2a45", "borderColor": "#334155",
                "textStyle": {"color": "#e2e8f0", "fontSize": 12},
            }
            base.update({
                "grid": {"height": "60%", "top": "12%", "left": "3%", "right": "8%", "containLabel": True},
                "xAxis": {"type": "category", "data": x_uniq, "splitArea": {"show": True},
                          "axisLabel": {"color": "#94a3b8", "rotate": 30, "fontSize": 10}},
                "yAxis": {"type": "category", "data": y_uniq, "splitArea": {"show": True},
                          "axisLabel": {"color": "#94a3b8", "fontSize": 10}},
                "visualMap": {
                    "min": zmin, "max": zmax, "calculable": True,
                    "orient": "horizontal", "left": "center", "bottom": "5%",
                    "textStyle": {"color": "#94a3b8"},
                    "inRange": {"color": ["#1e2a45", "#4338ca", "#6366f1", "#22d3ee", "#f59e0b"]},
                },
                "series": [{
                    "type": "heatmap", "data": data,
                    "label": {"show": False},
                    "emphasis": {"itemStyle": {"shadowBlur": 8, "shadowColor": "rgba(0,0,0,0.5)"}},
                }],
            })

    elif ct == "calendar_heatmap":
        # Expects x_col = date string, y_col = numeric. Auto-detects year.
        try:
            dseries = pd.to_datetime(df[spec.x], errors="coerce") if spec.x in df.columns else pd.Series([], dtype="datetime64[ns]")
            yseries = pd.to_numeric(df[spec.y], errors="coerce") if spec.y in df.columns else pd.Series([], dtype=float)
            valid = dseries.notna() & yseries.notna()
            paired = list(zip(dseries[valid].dt.strftime("%Y-%m-%d").tolist(),
                              yseries[valid].astype(float).tolist()))
        except Exception:
            paired = []
        if not paired:
            base.update({"xAxis": _x_axis(), "yAxis": _y_axis(),
                         "series": [{"type": "bar", "data": y_vals,
                                     "itemStyle": {"color": _v_gradient("#6366f1", "#4338ca")}}]})
        else:
            year = paired[0][0][:4]
            zvals = [v for _, v in paired]
            base.pop("toolbox", None)
            base.pop("grid", None)
            base["tooltip"] = {
                "position": "top",
                "backgroundColor": "#1e2a45", "borderColor": "#334155",
                "textStyle": {"color": "#e2e8f0", "fontSize": 12},
            }
            base.update({
                "visualMap": {
                    "min": min(zvals), "max": max(zvals),
                    "calculable": True, "orient": "horizontal",
                    "left": "center", "bottom": 8,
                    "textStyle": {"color": "#94a3b8"},
                    "inRange": {"color": ["#1e2a45", "#312e81", "#4338ca", "#6366f1", "#22d3ee"]},
                },
                "calendar": {
                    "top": 70, "left": 30, "right": 30,
                    "range": year, "cellSize": ["auto", 14],
                    "splitLine": {"show": False},
                    "yearLabel": {"show": True, "color": "#cbd5e1"},
                    "monthLabel": {"color": "#94a3b8"},
                    "dayLabel": {"color": "#94a3b8"},
                    "itemStyle": {"borderColor": "#0a0f1e", "borderWidth": 2, "color": "#1e2a45"},
                },
                "series": [{
                    "type": "heatmap", "coordinateSystem": "calendar", "data": paired,
                }],
            })

    # ── Bubble chart (scatter with size encoding) ───────────────────────────

    elif ct == "bubble":
        # x: numeric, y: numeric, size: 3rd numeric column or color_col.
        size_col = spec.color if spec.color and spec.color in df.columns else None
        if size_col is None:
            for c in df.columns:
                if c not in (spec.x, spec.y) and pd.api.types.is_numeric_dtype(df[c]):
                    size_col = c
                    break
        bubble_data: list = []
        if spec.x in df.columns and spec.y in df.columns:
            sizes_raw = pd.to_numeric(df[size_col], errors="coerce").fillna(1.0).abs() if size_col else pd.Series([1.0] * len(df))
            smin, smax = (float(sizes_raw.min()), float(sizes_raw.max())) if len(sizes_raw) else (0.0, 1.0)
            scale = (smax - smin) or 1.0
            for i, row in enumerate(df.itertuples(index=False)):
                try:
                    xv = float(getattr(row, spec.x))
                    yv = float(getattr(row, spec.y))
                except (TypeError, ValueError):
                    continue
                raw = float(sizes_raw.iloc[i]) if i < len(sizes_raw) else 1.0
                radius = 8 + 32 * (raw - smin) / scale
                bubble_data.append({"value": [xv, yv, raw], "symbolSize": radius})
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = (
            f"{{b}}<br/>{spec.x}: {{c[0]}}<br/>{spec.y}: {{c[1]}}"
            + (f"<br/>{size_col}: {{c[2]}}" if size_col else "")
        )
        base.update({
            "xAxis": {"type": "value", "name": spec.x or "X",
                      "nameTextStyle": {"color": "#94a3b8"},
                      "axisLabel": {"color": "#94a3b8"},
                      "splitLine": {"lineStyle": {"color": "#1e2a45", "type": "dashed"}}},
            "yAxis": {"type": "value", "name": spec.y or "Y",
                      "nameTextStyle": {"color": "#94a3b8"},
                      "axisLabel": {"color": "#94a3b8"},
                      "splitLine": {"lineStyle": {"color": "#1e2a45", "type": "dashed"}}},
            "series": [{
                "type": "scatter", "data": bubble_data,
                "itemStyle": {"color": "#6366f1", "opacity": 0.7,
                              "borderColor": "#22d3ee", "borderWidth": 1},
                "emphasis": {"itemStyle": {"opacity": 1, "shadowBlur": 12, "shadowColor": "#6366f1"}},
            }],
        })

    # ── Sankey (flow diagram) ───────────────────────────────────────────────

    elif ct == "sankey":
        # Expect 3 columns: source, target, value.
        src_col = spec.x or (columns[0] if len(columns) > 0 else "")
        tgt_col = spec.color or (columns[1] if len(columns) > 1 else "")
        val_col = spec.y or (columns[2] if len(columns) > 2 else "")
        nodes: list[dict] = []
        seen: set = set()
        links: list[dict] = []
        if all(c and c in df.columns for c in (src_col, tgt_col, val_col)):
            for row in df.itertuples(index=False):
                s = str(getattr(row, src_col))
                t = str(getattr(row, tgt_col))
                try:
                    v = float(getattr(row, val_col))
                except (TypeError, ValueError):
                    continue
                if s not in seen:
                    nodes.append({"name": s}); seen.add(s)
                if t not in seen:
                    nodes.append({"name": t}); seen.add(t)
                links.append({"source": s, "target": t, "value": v})
        base.pop("grid", None)
        base.pop("toolbox", None)
        base["tooltip"]["trigger"] = "item"
        base["series"] = [{
            "type": "sankey", "data": nodes, "links": links,
            "left": 30, "right": 80, "top": 60, "bottom": 30,
            "emphasis": {"focus": "adjacency"},
            "lineStyle": {"color": "gradient", "curveness": 0.55, "opacity": 0.6},
            "itemStyle": {"borderWidth": 0, "borderColor": "#0a0f1e"},
            "label": {"color": "#e2e8f0", "fontSize": 11},
            "levels": [
                {"depth": d, "itemStyle": {"color": COLORS[d % len(COLORS)]}}
                for d in range(8)
            ],
        }]

    # ── Candlestick (OHLC) ──────────────────────────────────────────────────

    elif ct == "candlestick":
        # Expect 5 columns: date, open, high, low, close (in that order).
        if len(columns) >= 5:
            date_col = columns[0]
            ohlc_cols = columns[1:5]
            try:
                ohlc_data: list[list[float]] = []
                for row in df.itertuples(index=False):
                    o = float(getattr(row, ohlc_cols[0]))
                    h = float(getattr(row, ohlc_cols[1]))
                    l = float(getattr(row, ohlc_cols[2]))
                    c = float(getattr(row, ohlc_cols[3]))
                    # ECharts expects [open, close, lowest, highest]
                    ohlc_data.append([o, c, l, h])
                date_vals = df[date_col].astype(str).tolist()
            except (TypeError, ValueError):
                ohlc_data, date_vals = [], []
        else:
            ohlc_data, date_vals = [], []

        base.update({
            "xAxis": {"type": "category", "data": date_vals,
                      "axisLabel": {"color": "#94a3b8", "fontSize": 10, "rotate": 30},
                      "axisLine": {"lineStyle": {"color": "#334155"}}},
            "yAxis": _y_axis(),
            "series": [{
                "type": "candlestick", "data": ohlc_data,
                "itemStyle": {
                    "color": "#10b981",         # bullish (close > open)
                    "color0": "#f43f5e",        # bearish
                    "borderColor": "#10b981",
                    "borderColor0": "#f43f5e",
                },
            }],
        })

    # ── Waterfall (cumulative deltas) ───────────────────────────────────────

    elif ct == "waterfall":
        # Two stacked bar series — invisible "placeholder" + visible delta.
        deltas = [float(v) if v is not None else 0.0 for v in y_vals]
        running: list[float] = []
        placeholder: list[float] = []
        positives: list[Any] = []
        negatives: list[Any] = []
        cum = 0.0
        for d in deltas:
            if d >= 0:
                placeholder.append(cum)
                positives.append(d)
                negatives.append("-")
            else:
                placeholder.append(cum + d)
                positives.append("-")
                negatives.append(-d)
            cum += d
            running.append(cum)
        base["tooltip"]["formatter"] = "{b}<br/>Δ: {c}"
        base.update({
            "xAxis": _x_axis(rotate=30),
            "yAxis": _y_axis(),
            "legend": {"data": ["Increase", "Decrease"],
                       "textStyle": {"color": "#cbd5e1"}, "top": 32},
            "series": [
                {
                    "name": "_placeholder",
                    "type": "bar", "stack": "total",
                    "itemStyle": {"borderColor": "transparent", "color": "transparent"},
                    "emphasis": {"itemStyle": {"borderColor": "transparent", "color": "transparent"}},
                    "data": placeholder,
                },
                {
                    "name": "Increase", "type": "bar", "stack": "total",
                    "data": positives,
                    "itemStyle": {"color": _v_gradient("#10b981", "#047857"),
                                  "borderRadius": [3, 3, 0, 0]},
                    "label": {"show": len(deltas) <= 14, "position": "top",
                              "color": "#10b981", "fontSize": 10},
                },
                {
                    "name": "Decrease", "type": "bar", "stack": "total",
                    "data": negatives,
                    "itemStyle": {"color": _v_gradient("#f43f5e", "#9f1239"),
                                  "borderRadius": [3, 3, 0, 0]},
                    "label": {"show": len(deltas) <= 14, "position": "top",
                              "color": "#f43f5e", "fontSize": 10},
                },
            ],
        })

    # ── Radar (multi-dimension comparison) ──────────────────────────────────

    elif ct == "radar":
        # Indicators come from x_col; series come from color_col groups.
        if spec.x in df.columns and spec.y in df.columns:
            indicator_names = list(dict.fromkeys(df[spec.x].astype(str).tolist()))
            try:
                _max_val = float(pd.to_numeric(df[spec.y], errors="coerce").max() or 1.0)
            except Exception:
                _max_val = 1.0
            indicators = [{"name": n, "max": _max_val * 1.1} for n in indicator_names]

            radar_series: list[dict] = []
            if spec.color and spec.color in df.columns:
                for i, (grp_name, grp) in enumerate(df.groupby(spec.color)):
                    values = [float(grp[grp[spec.x].astype(str) == n][spec.y].sum() or 0)
                              for n in indicator_names]
                    radar_series.append({
                        "name": str(grp_name), "value": values,
                        "itemStyle": {"color": COLORS[i % len(COLORS)]},
                        "areaStyle": {"opacity": 0.25},
                    })
            else:
                values = [float(df[df[spec.x].astype(str) == n][spec.y].sum() or 0)
                          for n in indicator_names]
                radar_series.append({
                    "name": spec.y, "value": values,
                    "itemStyle": {"color": "#6366f1"},
                    "areaStyle": {"opacity": 0.3},
                })
            base.pop("grid", None)
            base.pop("toolbox", None)
            base["tooltip"]["trigger"] = "item"
            base["legend"] = {"top": 32, "textStyle": {"color": "#cbd5e1"}}
            base["radar"] = {
                "indicator": indicators,
                "axisName": {"color": "#cbd5e1", "fontSize": 11},
                "splitLine": {"lineStyle": {"color": "#1e2a45"}},
                "splitArea": {"areaStyle": {"color": ["#0f1629", "#0a0f1e"]}},
                "axisLine": {"lineStyle": {"color": "#1e2a45"}},
                "center": ["50%", "55%"], "radius": "65%",
            }
            base["series"] = [{"type": "radar", "data": radar_series,
                               "lineStyle": {"width": 2}}]

    # ── Sunburst (hierarchical pie) ─────────────────────────────────────────

    elif ct == "sunburst":
        # Hierarchy: color_col (parent) → x_col (child), value = y_col.
        sunburst_root: list[dict] = []
        if spec.color and spec.color in df.columns and spec.x in df.columns and spec.y in df.columns:
            try:
                grouped = df.groupby([spec.color, spec.x])[spec.y].sum().reset_index()
                buckets: dict[str, list[dict]] = {}
                for row in grouped.itertuples(index=False):
                    parent = str(getattr(row, spec.color))
                    child  = str(getattr(row, spec.x))
                    try:
                        v = float(getattr(row, spec.y))
                    except (TypeError, ValueError):
                        continue
                    buckets.setdefault(parent, []).append({"name": child, "value": v})
                for i, (parent, children) in enumerate(buckets.items()):
                    sunburst_root.append({
                        "name": parent, "children": children,
                        "itemStyle": {"color": COLORS[i % len(COLORS)]},
                    })
            except Exception:
                sunburst_root = []
        if not sunburst_root:
            # 2-column fallback — flat sunburst from x/y
            sunburst_root = [
                {"name": str(x), "value": y, "itemStyle": {"color": COLORS[i % len(COLORS)]}}
                for i, (x, y) in enumerate(zip(x_vals, y_vals))
            ]
        base.pop("grid", None)
        base.pop("toolbox", None)
        base["tooltip"]["trigger"] = "item"
        base["tooltip"]["formatter"] = "{b}: {c}"
        base["series"] = [{
            "type": "sunburst", "data": sunburst_root,
            "radius": ["10%", "82%"], "center": ["50%", "55%"],
            "label": {"color": "#e2e8f0", "fontSize": 11},
            "itemStyle": {"borderColor": "#0a0f1e", "borderWidth": 2},
            "emphasis": {"focus": "ancestor"},
        }]

    else:
        # Fallback: vertical bar chart
        base.update({
            "xAxis": _x_axis(),
            "yAxis": _y_axis(),
            "series": [{
                "type": "bar",
                "data": y_vals,
                "itemStyle": {"color": _v_gradient("#6366f1", "#4338ca")},
            }],
        })

    return base
