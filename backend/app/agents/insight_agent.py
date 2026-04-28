"""
Insight Agent — turns verified query results into a grounded executive narrative.

Anti-hallucination design
-------------------------
The agent never sees raw sample rows. Instead it receives a ``DataFacts``
object computed directly from ALL result rows — real min/max/sum/avg/top-N
values with provenance. The LLM writes prose around these proven facts; it
cannot invent a number because no raw rows are shown.

Two layers enforce this:
  1. The system prompt forbids any number not present in DataFacts.
  2. The critic post-checks by extracting cited numbers from the narrative
     and cross-referencing them against the same DataFacts.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agents.intent_classifier import Intent
from app.agents.llm_factory import llm_for
from app.agents.planner import AnalysisPlan
from app.agents.sql_workers import QueryResult


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class InsightReport(BaseModel):
    """Executive narrative generated from verified data facts. All numbers must be traceable to DataFacts."""

    model_config = ConfigDict(populate_by_name=True)

    headline: str = Field(
        default="",
        description=(
            "Single newspaper-style headline ≤18 words. "
            "The most important business insight in one sharp sentence. "
            "Do not start with 'The data shows' or similar filler."
        ),
        max_length=200,
    )
    executive_summary: str = Field(
        default="",
        description=(
            "1–2 sentences that directly answer the user's question. "
            "Write as a business executive briefing, not a technical summary. "
            "Only cite numbers that appear in VERIFIED DATA FACTS."
        ),
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description=(
            "3–6 bullet points, each 1–2 sentences. "
            "Each finding must be supported by a number from VERIFIED DATA FACTS. "
            "No filler, no repetition, no phrases like 'the data reveals'."
        ),
        min_length=0,
        max_length=6,
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "0–3 outliers, surprises, or data quality concerns. "
            "Only include if there is genuine signal — empty list is fine. "
            "Do not invent anomalies."
        ),
        max_length=3,
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "0–3 concrete, actionable next steps for a business decision-maker. "
            "Must be specific (e.g. 'Investigate partner X which has 3× average fraud rate'). "
            "Never platitudes like 'improve data quality' or 'monitor the situation'."
        ),
        max_length=3,
    )
    latency_ms: int = Field(
        default=0,
        description=(
            "Pipeline instrumentation — time taken to generate this report in milliseconds. "
            "Set by the pipeline, not the LLM."
        ),
        exclude=True,  # exclude from LLM schema so the model never tries to fill it
    )


# ---------------------------------------------------------------------------
# Data grounding — compute verified facts from ALL result rows
# ---------------------------------------------------------------------------

class ColumnFacts(BaseModel):
    col_type: str           # "numeric" | "categorical" | "empty"
    row_count: int = 0
    # Numeric fields
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    sum_val: Optional[float] = None
    avg_val: Optional[float] = None
    all_unique_values: list[float] = Field(default_factory=list)
    # Categorical fields
    unique_count: Optional[int] = None
    top_values: dict[str, int] = Field(default_factory=dict)


class QueryFacts(BaseModel):
    query_id: str
    purpose: str
    rows_returned: int
    columns: dict[str, ColumnFacts] = Field(default_factory=dict)


def _compute_column_facts(col_name: str, values: list[Any]) -> ColumnFacts:
    non_null = [v for v in values if v is not None and str(v).strip() not in ("", "None", "null")]
    if not non_null:
        return ColumnFacts(col_type="empty", row_count=0)

    # Attempt numeric interpretation
    numeric: list[float] = []
    for v in non_null:
        try:
            numeric.append(float(str(v).replace(",", "").replace("%", "").strip()))
        except (ValueError, TypeError):
            pass

    if len(numeric) == len(non_null):
        unique_sorted = sorted(set(numeric))
        return ColumnFacts(
            col_type="numeric",
            row_count=len(numeric),
            min_val=min(numeric),
            max_val=max(numeric),
            sum_val=sum(numeric),
            avg_val=sum(numeric) / len(numeric),
            all_unique_values=unique_sorted[:50],  # cap at 50 unique values
        )

    # Categorical
    ctr = Counter(str(v) for v in non_null)
    return ColumnFacts(
        col_type="categorical",
        row_count=len(non_null),
        unique_count=len(ctr),
        top_values=dict(ctr.most_common(15)),
    )


def compute_data_facts(
    plan: AnalysisPlan,
    results: dict[str, QueryResult],
) -> list[QueryFacts]:
    """
    Compute verified statistical facts from ALL rows of every query result.
    This is the single authoritative data source for the insight agent and critic.
    """
    all_facts: list[QueryFacts] = []
    for q in plan.queries:
        r = results.get(q.id)
        if r is None or not r.success or not r.rows:
            all_facts.append(QueryFacts(
                query_id=q.id,
                purpose=q.purpose,
                rows_returned=0,
            ))
            continue

        col_facts: dict[str, ColumnFacts] = {}
        for col_idx, col_name in enumerate(r.columns):
            raw = [row[col_idx] if col_idx < len(row) else None for row in r.rows]
            col_facts[col_name] = _compute_column_facts(col_name, raw)

        all_facts.append(QueryFacts(
            query_id=q.id,
            purpose=q.purpose,
            rows_returned=r.rows_count,
            columns=col_facts,
        ))

    return all_facts


def _facts_to_prompt_block(facts: list[QueryFacts]) -> str:
    """Serialise DataFacts into a compact, LLM-readable block."""
    blocks: list[str] = []
    for qf in facts:
        if qf.rows_returned == 0:
            blocks.append(f"### {qf.query_id} — {qf.purpose}\n  (no data returned)")
            continue

        col_lines: list[str] = []
        for col_name, cf in qf.columns.items():
            if cf.col_type == "empty":
                col_lines.append(f"  {col_name}: (empty)")
            elif cf.col_type == "numeric":
                vals_str = ", ".join(
                    str(int(v) if v == int(v) else round(v, 4))
                    for v in (cf.all_unique_values or [])
                )
                col_lines.append(
                    f"  {col_name} [numeric]: "
                    f"count={cf.row_count}, "
                    f"min={cf.min_val}, max={cf.max_val}, "
                    f"sum={round(cf.sum_val or 0, 4)}, avg={round(cf.avg_val or 0, 4)}"
                    + (f", values=[{vals_str}]" if cf.all_unique_values else "")
                )
            else:  # categorical
                top_str = ", ".join(f'"{k}":{v}' for k, v in list(cf.top_values.items())[:10])
                col_lines.append(
                    f"  {col_name} [categorical]: "
                    f"count={cf.row_count}, unique={cf.unique_count}, "
                    f"top={{{ top_str }}}"
                )

        blocks.append(
            f"### {qf.query_id} — {qf.purpose}\n"
            f"  rows_returned: {qf.rows_returned}\n"
            + "\n".join(col_lines)
        )

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior business analyst writing an executive narrative for a data report.

You receive VERIFIED DATA FACTS — statistical summaries computed directly from
actual query results (min, max, sum, avg, all unique values, top category counts).
These are the ONLY numbers you are permitted to cite.

ABSOLUTE RULES (violation = hallucination):
============================================

1. CITE ONLY numbers that appear in VERIFIED DATA FACTS.
   Allowed transformations:
     • Round to nearest K: 171,258 → "171K"  (fine — ≤0.5K rounding)
     • Round to 0.1M: 3,283,824 → "3.3M"    (fine)
     • Percentage conversion: 0.937 → "93.7%" (fine)
   NOT allowed:
     • Inventing any number not traceable to a VERIFIED DATA FACT
     • Estimating, extrapolating, or interpolating values
   Year references (2024, 2026, etc.) are temporal context — not data values; freely usable.

2. If rows_returned=0 for a query: write ONLY "No data available for [topic]."
   Do NOT say "the rate is 0%" unless rows > 0 and the value is actually 0.

3. NEVER use: "the data shows", "the analysis reveals", "SQL", "query", or any
   technical terminology. Write as a business stakeholder briefing an executive.

4. Bullets (key_findings): exactly 1–2 sentences each. No padding, no filler.

5. Headline: newspaper headline style — the single sharpest business insight.
   ≤18 words. No "The" as first word if avoidable.

6. Recommendations: concrete actions only.
   Good: "Investigate Insurance category which drives 37% of volume with 0% success."
   Bad:  "Monitor the situation and improve data quality."
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


def _coerce_str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_insights(
    question: str,
    intent: Intent,
    plan: AnalysisPlan,
    results: dict[str, QueryResult],
    critique_feedback: Optional[list[Any]] = None,
    data_facts: Optional[list[QueryFacts]] = None,
) -> InsightReport:
    """
    Produce an InsightReport grounded in verified data facts.

    Parameters
    ----------
    critique_feedback:
        Error-level issues from a previous critic run. Injected into the
        prompt so the LLM can correct specific problems on retry.
    data_facts:
        Pre-computed facts (pass to avoid recomputation on retries).
    """
    t0 = time.perf_counter()
    elapsed = lambda: int((time.perf_counter() - t0) * 1000)

    successful = [r for r in results.values() if r.success]
    if not successful:
        return InsightReport(
            headline="No data returned — all queries failed.",
            executive_summary=(
                "The analysis could not be completed because no query returned data. "
                "Check that the connected database has the expected tables and columns."
            ),
            latency_ms=elapsed(),
        )

    # Compute or reuse verified facts
    facts = data_facts if data_facts is not None else compute_data_facts(plan, results)
    facts_block = _facts_to_prompt_block(facts)

    user_msg = (
        f"## Original question\n\n{question}\n\n"
        f"## Analysis title\n\n{plan.title}\n\n"
        f"## Analysis goal\n\n{plan.description}\n\n"
        f"## VERIFIED DATA FACTS\n"
        f"(Computed from actual query results — cite ONLY these numbers)\n\n"
        f"{facts_block}\n"
    )

    if critique_feedback:
        issues_text = "\n".join(
            f"  [{getattr(i, 'severity', 'error').upper()}]"
            + (f" ({getattr(i, 'location', '') or ''})" if getattr(i, 'location', '') else "")
            + f": {getattr(i, 'message', str(i))}"
            for i in critique_feedback
        )
        user_msg += (
            f"\n## CORRECTION REQUIRED — previous answer failed quality review\n\n"
            f"Fix ALL of the following issues in your new response:\n\n"
            f"{issues_text}\n\n"
            f"Every number you cite MUST appear in VERIFIED DATA FACTS above.\n"
        )

    llm = llm_for("insight_agent")
    structured_llm = llm.with_structured_output(InsightReport, include_raw=True)

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(result["raw"])

        if result["parsed"] is not None and result["parsing_error"] is None:
            return result["parsed"].model_copy(update={"latency_ms": elapsed()})

        raw_text = str(result["raw"].content)
    except Exception as exc:
        return InsightReport(
            headline=plan.title,
            executive_summary=f"Insight agent unavailable ({exc.__class__.__name__}).",
            latency_ms=elapsed(),
        )

    # Fallback: manual parse when structured output fails to produce a parsed model
    parsed = _parse_json_lenient(raw_text)
    if parsed is None:
        return InsightReport(
            headline=plan.title,
            executive_summary="Insight agent output could not be parsed.",
            latency_ms=elapsed(),
        )

    return InsightReport(
        headline=str(parsed.get("headline") or plan.title)[:200],
        executive_summary=str(parsed.get("executive_summary") or "").strip(),
        key_findings=_coerce_str_list(parsed.get("key_findings")),
        anomalies=_coerce_str_list(parsed.get("anomalies")),
        recommendations=_coerce_str_list(parsed.get("recommendations")),
        latency_ms=elapsed(),
    )
