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
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": "12%",
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
            "label": {"color": "#e2e8f0", "fontSize": 11},
            "labelLine": {"lineStyle": {"color": "#475569"}},
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
        base.update({
            "xAxis": _x_axis(),
            "yAxis": _y_axis(),
            "series": [{
                "type": "boxplot",
                "data": [],
                "itemStyle": {"color": "#6366f1", "borderColor": "#818cf8"},
            }],
        })

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
