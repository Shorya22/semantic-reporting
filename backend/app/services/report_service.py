"""
Report Composer — turns the multi-agent pipeline output into PDF / XLSX reports.

Inputs (always the same shape):
    title       : the analysis title (PlanInfo.title)
    description : 1-2 sentence summary
    visuals     : list[RenderedVisual] from the Viz Designer
    results     : dict[query_id -> QueryResult] from the SQL Workers
    insight     : InsightReport from the Insight Agent
    question    : the original user question (shown on the cover page)

Outputs:
    compose_pdf_report  -> bytes  (application/pdf)
    compose_xlsx_report -> bytes  (application/vnd.openxmlformats-…)

Why this stack
--------------
* **fpdf2** — pure Python PDF, no system deps. WeasyPrint would be prettier
  but needs GTK/Cairo on Windows which is painful to install.
* **xlsxwriter** — pure Python, generates NATIVE Excel charts (not PNG
  screenshots), conditional formatting, KPI tiles via shapes.
* **Plotly + Kaleido** (already installed) — renders chart PNGs for the
  PDF body since fpdf2 has no chart engine of its own.

Look & feel
-----------
Dark navy cover, bright accent colour (#6366f1), Inter-style sans-serif.
Tries to feel like a Looker / Tableau executive PDF, not a 1990s SAS report.
"""

from __future__ import annotations

import base64
import io
import textwrap
from datetime import datetime, timezone
from typing import Any, Optional

from fpdf import FPDF
import xlsxwriter

from app.agents.insight_agent import InsightReport
from app.agents.planner import AnalysisPlan
from app.agents.sql_workers import QueryResult
from app.agents.viz_designer import RenderedVisual
from app.services.viz_service import ChartSpec, render_chart


# ===========================================================================
# Theme constants
# ===========================================================================

PRIMARY_COLOR  = (99, 102, 241)        # indigo-500   #6366f1
DARK_BG        = (10, 15, 30)          # near-black for cover
DARK_CARD      = (24, 32, 56)
TEXT_LIGHT     = (226, 232, 240)
TEXT_DIM       = (148, 163, 184)
TEXT_MUTED     = (100, 116, 139)
ACCENT_OK      = (16, 185, 129)        # emerald
ACCENT_WARN    = (245, 158, 11)        # amber
ACCENT_ERR     = (239, 68, 68)         # red

PAGE_W_MM, PAGE_H_MM = 297, 210        # A4 landscape
MARGIN_MM            = 12


# ===========================================================================
# Helpers
# ===========================================================================

def _safe_text(s: Any, max_len: int = 600) -> str:
    """fpdf2 default font is Helvetica which is latin-1; replace exotics."""
    if s is None:
        return ""
    out = str(s)
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    # Replace Unicode characters that the default Helvetica font can't render.
    # fpdf2 will raise UnicodeEncodeError otherwise.
    return (
        out
        .replace("—", "-")          # em dash
        .replace("–", "-")          # en dash
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("•", "*")
        .replace("…", "...")
        .replace(" ", " ")
        .replace("→", "->")
        .replace("₹", "INR ")        # rupee sign
        .replace("°", " deg ")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


# ===========================================================================
# PDF report
# ===========================================================================

class _ReportPDF(FPDF):
    """FPDF subclass with cover + page footer styling baked in."""

    def __init__(self, *, title: str, generated_at: datetime):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=MARGIN_MM)
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self._report_title = title
        self._generated_at = generated_at

    def header(self):
        # Suppress on the cover page itself
        if self.page_no() <= 1:
            return
        self.set_font("helvetica", "", 8)
        self.set_text_color(*TEXT_MUTED)
        self.set_y(6)
        self.cell(0, 4, _safe_text(self._report_title), align="L")
        self.set_y(6)
        self.cell(0, 4, "DataLens AI", align="R")
        self.set_draw_color(*TEXT_MUTED)
        self.set_line_width(0.1)
        self.line(MARGIN_MM, 11, PAGE_W_MM - MARGIN_MM, 11)

    def footer(self):
        if self.page_no() <= 1:
            return
        self.set_y(-9)
        self.set_font("helvetica", "", 7)
        self.set_text_color(*TEXT_MUTED)
        self.cell(0, 4, _safe_text(self._generated_at.strftime("%Y-%m-%d %H:%M UTC")), align="L")
        self.set_y(-9)
        self.cell(0, 4, f"Page {self.page_no()}", align="R")


def _draw_cover(pdf: _ReportPDF, *, title: str, question: str,
                description: str, generated_at: datetime) -> None:
    pdf.add_page()
    # Dark fill
    pdf.set_fill_color(*DARK_BG)
    pdf.rect(0, 0, PAGE_W_MM, PAGE_H_MM, "F")

    # Accent stripe
    pdf.set_fill_color(*PRIMARY_COLOR)
    pdf.rect(0, 0, 6, PAGE_H_MM, "F")

    # Eyebrow
    pdf.set_xy(30, 60)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(*PRIMARY_COLOR)
    pdf.cell(0, 6, _safe_text("DATALENS AI  -  ANALYSIS REPORT"))

    # Title
    pdf.set_xy(30, 72)
    pdf.set_font("helvetica", "B", 32)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.multi_cell(PAGE_W_MM - 60, 14, _safe_text(title or "Untitled analysis"))

    # Description
    if description:
        pdf.set_x(30)
        pdf.set_font("helvetica", "", 12)
        pdf.set_text_color(*TEXT_DIM)
        pdf.multi_cell(PAGE_W_MM - 60, 6, _safe_text(description))

    # Question
    pdf.set_xy(30, PAGE_H_MM - 60)
    pdf.set_font("helvetica", "I", 10)
    pdf.set_text_color(*TEXT_DIM)
    pdf.cell(0, 5, _safe_text("Original question"))
    pdf.set_xy(30, PAGE_H_MM - 54)
    pdf.set_font("helvetica", "", 12)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.multi_cell(PAGE_W_MM - 60, 6, _safe_text(f'"{question}"'))

    # Footer
    pdf.set_xy(30, PAGE_H_MM - 22)
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(0, 4, _safe_text(generated_at.strftime("Generated %Y-%m-%d  %H:%M UTC")))


def _draw_kpi_strip(pdf: _ReportPDF, kpis: list[RenderedVisual]) -> None:
    """Lay KPIs out as 3-4 cards in a row near the top of a page."""
    if not kpis:
        return
    pdf.add_page()
    pdf.set_y(20)
    pdf.set_font("helvetica", "B", 16)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.cell(0, 8, _safe_text("Key metrics"))
    pdf.ln(12)

    n = min(len(kpis), 4)                                 # cap at 4 per strip
    available = PAGE_W_MM - 2 * MARGIN_MM - (n - 1) * 4   # 4mm gutter
    card_w = available / n
    card_h = 36
    y0 = pdf.get_y()
    for i, v in enumerate(kpis[:n]):
        x = MARGIN_MM + i * (card_w + 4)
        # Card background
        pdf.set_fill_color(*DARK_CARD)
        pdf.set_draw_color(*PRIMARY_COLOR)
        pdf.set_line_width(0.2)
        pdf.rect(x, y0, card_w, card_h, "FD")
        # Label
        pdf.set_xy(x + 4, y0 + 4)
        pdf.set_font("helvetica", "B", 7)
        pdf.set_text_color(*TEXT_DIM)
        pdf.cell(card_w - 8, 4, _safe_text((v.title or v.kpi.label).upper() if v.kpi else "—"))
        # Value
        pdf.set_xy(x + 4, y0 + 14)
        pdf.set_font("helvetica", "B", 22)
        pdf.set_text_color(*TEXT_LIGHT)
        val = v.kpi.formatted_value if v.kpi else "—"
        pdf.cell(card_w - 8, 10, _safe_text(val))
        # Subtitle / unit
        if v.subtitle or (v.kpi and v.kpi.unit):
            pdf.set_xy(x + 4, y0 + 26)
            pdf.set_font("helvetica", "", 7)
            pdf.set_text_color(*TEXT_MUTED)
            sub = v.subtitle or (v.kpi.unit if v.kpi else "")
            pdf.cell(card_w - 8, 4, _safe_text(sub or ""))


def _pick_xy_from_result(columns: list[str],
                         rows: list[list[Any]]) -> tuple[str, str]:
    """First non-numeric column = x, first numeric column = y."""
    def _is_num(idx: int) -> bool:
        for r in rows[:30]:
            if idx >= len(r): continue
            v = r[idx]
            if v is None: continue
            try: float(v); return True
            except (TypeError, ValueError): return False
        return False

    x = ""
    y = ""
    for i, c in enumerate(columns):
        if not _is_num(i) and not x:
            x = c
        elif _is_num(i) and not y:
            y = c
    if not x and columns:
        x = columns[0]
    if not y and len(columns) > 1:
        y = columns[1]
    elif not y and columns:
        y = columns[0]
    return x, y


def _render_visual_png(visual: RenderedVisual,
                       results: dict[str, QueryResult],
                       width_px: int = 1100,
                       height_px: int = 460) -> Optional[bytes]:
    """Render a chart visual to PNG bytes via Plotly+Kaleido. KPIs/tables → None."""
    if visual.type in ("kpi", "table"):
        return None
    src = results.get(visual.from_query)
    if not src or not src.success or not src.rows:
        return None
    x, y = _pick_xy_from_result(src.columns, src.rows)
    spec = ChartSpec(
        chart_type=visual.type,
        title=visual.title,
        x=x,
        y=y,
    )
    try:
        b64 = render_chart(spec, src.rows, src.columns, width=width_px, height=height_px)
        return base64.b64decode(b64)
    except Exception:
        return None


def _draw_chart_page(pdf: _ReportPDF, visual: RenderedVisual,
                     results: dict[str, QueryResult]) -> None:
    """One chart per page, with its data table preview underneath."""
    png = _render_visual_png(visual, results)
    pdf.add_page()
    # Heading
    pdf.set_y(20)
    pdf.set_font("helvetica", "B", 14)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.cell(0, 7, _safe_text(visual.title or "Chart"))
    if visual.subtitle:
        pdf.ln(7)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(*TEXT_DIM)
        pdf.cell(0, 4, _safe_text(visual.subtitle))
    pdf.ln(8)

    # Chart image
    if png:
        img_io = io.BytesIO(png)
        pdf.image(img_io, x=MARGIN_MM, w=PAGE_W_MM - 2 * MARGIN_MM)
    else:
        pdf.set_font("helvetica", "I", 10)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(0, 6, _safe_text("(chart could not be rendered)"))


def _draw_table_page(pdf: _ReportPDF, visual: RenderedVisual) -> None:
    pdf.add_page()
    pdf.set_y(20)
    pdf.set_font("helvetica", "B", 14)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.cell(0, 7, _safe_text(visual.title or "Data table"))
    pdf.ln(10)

    cols = visual.table_columns
    rows = visual.table_rows[:30]
    if not cols:
        return

    col_w = (PAGE_W_MM - 2 * MARGIN_MM) / max(1, len(cols))
    # Header
    pdf.set_fill_color(*DARK_CARD)
    pdf.set_text_color(*TEXT_DIM)
    pdf.set_font("helvetica", "B", 8)
    for c in cols:
        pdf.cell(col_w, 7, _safe_text(c)[:30], border=0, fill=True)
    pdf.ln(7)
    # Body
    pdf.set_font("helvetica", "", 8)
    pdf.set_text_color(*TEXT_LIGHT)
    for ri, row in enumerate(rows):
        for v in row:
            pdf.cell(col_w, 6, _safe_text(v)[:30], border=0)
        pdf.ln(6)
        if ri >= 28:
            break
    # Truncation note
    if len(visual.table_rows) > 30:
        pdf.ln(3)
        pdf.set_font("helvetica", "I", 7)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(0, 4, _safe_text(f"({len(visual.table_rows)} total rows; first 30 shown)"))


def _draw_insights_page(pdf: _ReportPDF, insight: InsightReport) -> None:
    pdf.add_page()
    pdf.set_y(20)
    pdf.set_font("helvetica", "B", 18)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.cell(0, 8, _safe_text("Insights"))
    pdf.ln(11)

    if insight.headline:
        pdf.set_font("helvetica", "B", 12)
        pdf.set_text_color(*PRIMARY_COLOR)
        pdf.multi_cell(0, 6, _safe_text(insight.headline))
        pdf.ln(2)

    if insight.executive_summary:
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(*TEXT_LIGHT)
        pdf.multi_cell(0, 5, _safe_text(insight.executive_summary, max_len=2000))
        pdf.ln(4)

    def _section(label: str, items: list[str], color: tuple[int, int, int]) -> None:
        if not items:
            return
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(*color)
        pdf.cell(0, 6, _safe_text(label.upper()))
        pdf.ln(6)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(*TEXT_LIGHT)
        for it in items:
            pdf.cell(4, 5, "*", align="L")
            pdf.multi_cell(0, 5, _safe_text(it, max_len=600))
            pdf.ln(1)
        pdf.ln(3)

    _section("Key findings",   insight.key_findings,   ACCENT_OK)
    _section("Anomalies",      insight.anomalies,      ACCENT_WARN)
    _section("Recommendations", insight.recommendations, PRIMARY_COLOR)


def _draw_appendix_page(pdf: _ReportPDF, plan: AnalysisPlan,
                        results: dict[str, QueryResult]) -> None:
    pdf.add_page()
    pdf.set_y(20)
    pdf.set_font("helvetica", "B", 14)
    pdf.set_text_color(*TEXT_LIGHT)
    pdf.cell(0, 7, _safe_text("Appendix - SQL provenance"))
    pdf.ln(11)
    for q in plan.queries:
        r = results.get(q.id)
        pdf.set_font("helvetica", "B", 9)
        pdf.set_text_color(*PRIMARY_COLOR)
        pdf.cell(0, 5, _safe_text(f"[{q.id}] {q.purpose}"))
        pdf.ln(5)
        if r:
            pdf.set_font("helvetica", "I", 8)
            pdf.set_text_color(*TEXT_MUTED)
            status = "OK" if r.success else "FAIL"
            extras = f" * rows={r.rows_count} * latency={r.latency_ms} ms"
            if r.repaired:
                extras += " * (repaired)"
            pdf.cell(0, 4, _safe_text(f"  {status}{extras}"))
            pdf.ln(5)
        pdf.set_font("courier", "", 7)
        pdf.set_text_color(*TEXT_LIGHT)
        sql_text = (r.sql if r else q.sql).strip()
        for line in textwrap.wrap(sql_text, width=140):
            pdf.cell(0, 3.5, _safe_text(line))
            pdf.ln(3.5)
        pdf.ln(3)


# ---- Public PDF entry point ------------------------------------------------

def compose_pdf_report(
    *,
    question: str,
    plan: AnalysisPlan,
    results: dict[str, QueryResult],
    visuals: list[RenderedVisual],
    insight: InsightReport,
) -> bytes:
    """Generate a multi-page A4-landscape PDF report. Returns raw bytes."""
    title = plan.title or "Analysis"
    description = plan.description or ""
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)

    pdf = _ReportPDF(title=title, generated_at=generated_at)
    _draw_cover(pdf, title=title, question=question, description=description,
                generated_at=generated_at)

    kpis    = [v for v in visuals if v.type == "kpi"]
    tables  = [v for v in visuals if v.type == "table"]
    charts  = [v for v in visuals if v.type not in ("kpi", "table")]

    if kpis:
        _draw_kpi_strip(pdf, kpis)

    _draw_insights_page(pdf, insight)

    for chart in charts:
        _draw_chart_page(pdf, chart, results)

    for tbl in tables:
        _draw_table_page(pdf, tbl)

    _draw_appendix_page(pdf, plan, results)

    out = pdf.output(dest="S")
    if isinstance(out, str):
        return out.encode("latin-1")
    return bytes(out)


# ===========================================================================
# XLSX report
# ===========================================================================

# xlsxwriter chart_type mapping from Renderedvisual.type
_XLSX_CHART_MAP: dict[str, tuple[str, Optional[str]]] = {
    "bar":            ("column", None),
    "horizontal_bar": ("bar", None),
    "line":           ("line", None),
    "area":           ("area", None),
    "pie":            ("pie", None),
    "donut":          ("doughnut", None),
    "scatter":        ("scatter", "marker_only"),
    "funnel":         ("column", None),
    "treemap":        ("column", None),
    "histogram":      ("column", None),
    "gauge":          ("doughnut", None),
}


def _write_summary_sheet(wb: xlsxwriter.Workbook, ws,
                         *, title: str, description: str, question: str,
                         insight: InsightReport,
                         kpis: list[RenderedVisual]) -> None:
    # Formats
    title_fmt   = wb.add_format({"bold": True, "font_size": 22, "font_color": "#0f1629"})
    h2_fmt      = wb.add_format({"bold": True, "font_size": 14, "font_color": "#1e293b"})
    h3_fmt      = wb.add_format({"bold": True, "font_size": 11, "font_color": "#475569"})
    body_fmt    = wb.add_format({"font_size": 10, "font_color": "#1e293b", "text_wrap": True, "valign": "top"})
    label_fmt   = wb.add_format({
        "bold": True, "font_size": 8, "font_color": "#64748b",
        "bg_color": "#f1f5f9", "border": 1, "border_color": "#e2e8f0",
        "align": "left", "valign": "vcenter",
    })
    value_fmt   = wb.add_format({
        "bold": True, "font_size": 18, "font_color": "#1e293b",
        "bg_color": "#f8fafc", "border": 1, "border_color": "#e2e8f0",
        "align": "left", "valign": "vcenter",
    })
    sub_fmt     = wb.add_format({
        "italic": True, "font_size": 8, "font_color": "#94a3b8",
        "bg_color": "#f8fafc", "border": 1, "border_color": "#e2e8f0",
    })
    bullet_fmt  = wb.add_format({"font_size": 10, "font_color": "#334155", "text_wrap": True, "valign": "top"})

    ws.set_column("A:A", 2)
    ws.set_column("B:I", 18)

    ws.write("B2", title, title_fmt)
    ws.set_row(1, 30)
    if description:
        ws.merge_range("B3:I3", description, body_fmt)
    if question:
        ws.merge_range("B4:I4", f'Question: "{question}"', body_fmt)

    # KPI strip — start at row 6
    if kpis:
        ws.merge_range("B6:I6", "KEY METRICS", h3_fmt)
        col = 1
        n = min(4, len(kpis))
        each_w = 2  # 2 columns wide each
        row = 7
        for i, v in enumerate(kpis[:n]):
            c0 = col + i * each_w
            label = (v.title or (v.kpi.label if v.kpi else "")).upper()
            value = v.kpi.formatted_value if v.kpi else "—"
            sub = (v.subtitle or (v.kpi.unit if v.kpi else "") or "")
            ws.merge_range(row, c0, row, c0 + each_w - 1, label, label_fmt)
            ws.merge_range(row + 1, c0, row + 2, c0 + each_w - 1, value, value_fmt)
            ws.merge_range(row + 3, c0, row + 3, c0 + each_w - 1, sub, sub_fmt)
        ws.set_row(row + 1, 22)
        ws.set_row(row + 2, 22)

    # Insights — below the KPIs
    base = 13
    ws.merge_range(f"B{base}:I{base}", "EXECUTIVE SUMMARY", h2_fmt)
    if insight.headline:
        ws.merge_range(f"B{base + 1}:I{base + 1}", insight.headline, h3_fmt)
    ws.merge_range(f"B{base + 2}:I{base + 4}", insight.executive_summary or "", body_fmt)

    sec_row = base + 6
    sections = [
        ("KEY FINDINGS",     insight.key_findings),
        ("ANOMALIES",        insight.anomalies),
        ("RECOMMENDATIONS",  insight.recommendations),
    ]
    for label, items in sections:
        if not items:
            continue
        ws.merge_range(sec_row, 1, sec_row, 8, label, h3_fmt)
        for it in items:
            sec_row += 1
            ws.write(sec_row, 1, "*")
            ws.merge_range(sec_row, 2, sec_row, 8, it, bullet_fmt)
        sec_row += 2

    ws.set_zoom(110)


def _write_query_sheet(wb: xlsxwriter.Workbook, ws,
                       visual: RenderedVisual,
                       result: QueryResult) -> None:
    title_fmt  = wb.add_format({"bold": True, "font_size": 14, "font_color": "#0f1629"})
    head_fmt   = wb.add_format({
        "bold": True, "font_size": 9, "font_color": "#ffffff",
        "bg_color": "#4338ca", "border": 1, "border_color": "#3730a3",
        "align": "left", "valign": "vcenter",
    })
    cell_fmt   = wb.add_format({
        "font_size": 10, "font_color": "#1e293b", "border": 1,
        "border_color": "#e2e8f0",
    })
    cell_alt   = wb.add_format({
        "font_size": 10, "font_color": "#1e293b", "border": 1,
        "border_color": "#e2e8f0", "bg_color": "#f8fafc",
    })

    ws.set_column("A:A", 2)
    ws.set_column("B:B", 24)
    ws.set_column("C:M", 16)

    ws.write("B2", visual.title or "Data", title_fmt)

    cols = result.columns
    rows = result.rows
    if not cols or not rows:
        ws.write("B4", "(no data)", cell_fmt)
        return

    # Header
    for i, c in enumerate(cols):
        ws.write(3, 1 + i, c, head_fmt)
    # Rows (cap at 5000 — Excel handles way more but UX matters)
    for ri, row in enumerate(rows[:5000]):
        fmt = cell_fmt if ri % 2 == 0 else cell_alt
        for ci, val in enumerate(row):
            if val is None:
                ws.write(4 + ri, 1 + ci, "", fmt)
            elif isinstance(val, (int, float)):
                ws.write_number(4 + ri, 1 + ci, val, fmt)
            else:
                ws.write_string(4 + ri, 1 + ci, str(val), fmt)

    # Native Excel chart, when applicable
    chart_kind = _XLSX_CHART_MAP.get(visual.type)
    if chart_kind and len(cols) >= 2 and len(rows) > 0:
        chart = wb.add_chart({
            "type": chart_kind[0],
            **({"subtype": chart_kind[1]} if chart_kind[1] else {}),
        })
        last_row = 4 + min(len(rows), 5000) - 1
        sheet_name = ws.get_name()
        # Pick first non-numeric col for X, first numeric col for Y
        num_idx = None
        cat_idx = None
        for i, c in enumerate(cols):
            sample = next((r[i] for r in rows[:20] if r[i] is not None), None)
            is_num = isinstance(sample, (int, float))
            if is_num and num_idx is None:
                num_idx = i
            elif not is_num and cat_idx is None:
                cat_idx = i
        if cat_idx is None:
            cat_idx = 0
        if num_idx is None:
            num_idx = min(1, len(cols) - 1)
        chart.add_series({
            "name": cols[num_idx],
            "categories": [sheet_name, 4, 1 + cat_idx, last_row, 1 + cat_idx],
            "values":     [sheet_name, 4, 1 + num_idx, last_row, 1 + num_idx],
            "fill":       {"color": "#6366f1"},
            "border":     {"color": "#4338ca"},
        })
        chart.set_title({"name": visual.title or ""})
        chart.set_legend({"none": True})
        chart.set_size({"width": 720, "height": 420})
        ws.insert_chart("O4", chart)


def _write_appendix_sheet(wb: xlsxwriter.Workbook, ws,
                          plan: AnalysisPlan,
                          results: dict[str, QueryResult]) -> None:
    title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "#0f1629"})
    label_fmt = wb.add_format({"bold": True, "font_size": 9, "font_color": "#475569"})
    body_fmt  = wb.add_format({"font_size": 9, "font_color": "#1e293b", "text_wrap": True, "valign": "top"})
    sql_fmt   = wb.add_format({
        "font_name": "Consolas", "font_size": 9, "font_color": "#0f1629",
        "bg_color": "#f1f5f9", "text_wrap": True, "valign": "top",
        "border": 1, "border_color": "#e2e8f0",
    })

    ws.set_column("A:A", 2)
    ws.set_column("B:B", 14)
    ws.set_column("C:C", 90)

    ws.write("B2", "SQL Provenance", title_fmt)
    ws.write("B4", "Query ID", label_fmt)
    ws.write("C4", "SQL", label_fmt)

    row = 5
    for q in plan.queries:
        r = results.get(q.id)
        ws.write(row, 1, q.id, body_fmt)
        sql_text = (r.sql if r else q.sql).strip()
        ws.write(row, 2, sql_text, sql_fmt)
        ws.set_row(row, max(25, min(150, sql_text.count("\n") * 12 + 25)))
        row += 1


# ---- Public XLSX entry point ----------------------------------------------

def compose_xlsx_report(
    *,
    question: str,
    plan: AnalysisPlan,
    results: dict[str, QueryResult],
    visuals: list[RenderedVisual],
    insight: InsightReport,
) -> bytes:
    """Generate a multi-sheet styled Excel workbook. Returns raw bytes."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    # ── Sheet 1: Summary ────────────────────────────────────────────
    summary = wb.add_worksheet("Summary")
    summary.hide_gridlines(2)
    kpis = [v for v in visuals if v.type == "kpi"]
    _write_summary_sheet(
        wb, summary,
        title=plan.title or "Analysis",
        description=plan.description or "",
        question=question,
        insight=insight,
        kpis=kpis,
    )

    # ── Sheets 2..N: One per non-KPI visual ─────────────────────────
    used_names: set[str] = {"Summary"}
    for v in visuals:
        if v.type == "kpi":
            continue
        result = results.get(v.from_query)
        if result is None:
            continue
        # Excel sheet name max 31 chars, no /\?*[]:
        name_base = (v.title or v.visual_id).strip()
        for ch in r'/\?*[]:':
            name_base = name_base.replace(ch, " ")
        name_base = (name_base[:28] or v.visual_id)[:31]
        name = name_base
        i = 1
        while name in used_names:
            i += 1
            suf = f" {i}"
            name = name_base[: 31 - len(suf)] + suf
        used_names.add(name)
        ws = wb.add_worksheet(name)
        ws.hide_gridlines(2)
        _write_query_sheet(wb, ws, v, result)

    # ── Final sheet: Appendix ───────────────────────────────────────
    appendix = wb.add_worksheet("Appendix")
    appendix.hide_gridlines(2)
    _write_appendix_sheet(wb, appendix, plan, results)

    wb.close()
    return buf.getvalue()
