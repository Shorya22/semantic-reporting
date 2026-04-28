"""
Critic Agent — two-layer quality gate on the final pipeline output.

Layer 1 — Programmatic (zero token cost)
-----------------------------------------
Extracts every number cited in the insight narrative and cross-references
each against the verified DataFacts computed from ALL actual result rows.
Numbers that cannot be traced back to real data are flagged immediately.

Layer 2 — LLM semantic review
------------------------------
The LLM receives the same verified DataFacts (not sample rows) and checks:
  * Hallucinated numbers not in DataFacts
  * Visual labels / units that misrepresent underlying columns
  * Parts of the question not addressed by the plan
  * Empty result sets misrepresented as having data

If any issue has severity="error", ``CritiqueReport.passed`` is False and
the orchestrator regenerates the insight with corrective feedback.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.insight_agent import InsightReport, QueryFacts, _facts_to_prompt_block
from app.agents.intent_classifier import Intent
from app.agents.llm_factory import llm_for
from app.agents.planner import AnalysisPlan
from app.agents.sql_workers import QueryResult


Severity = Literal["info", "warning", "error"]


class Issue(BaseModel):
    severity: Severity = Field(description="info | warning | error")
    category: str = Field(description="hallucination | mis_label | unaddressed | empty | other")
    message: str = Field(description="One sentence describing the problem.")
    location: Optional[str] = Field(default=None, description="visual_id / query_id / 'insight'")


class CritiqueReport(BaseModel):
    passed: bool = Field(description="True if no error-level issues were found.")
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    issues: list[Issue] = Field(default_factory=list)
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Layer 1 — Programmatic number verification
# ---------------------------------------------------------------------------

_CITED_NUM_RE = re.compile(
    # Comma-separated thousands (e.g. "500,000") MUST have at least one comma
    # group, otherwise "2026" would incorrectly match as "202" via \d{1,3}.
    # Plain integers / decimals are caught by the second alternative.
    r"(?<![a-zA-Z0-9/])"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*([KkMmBbGg%])?"
    r"(?![a-zA-Z0-9])",
)


def _parse_cited_number(raw_num: str, suffix: str) -> Optional[float]:
    try:
        n = float(raw_num.replace(",", ""))
    except ValueError:
        return None
    s = (suffix or "").upper()
    if s == "K":
        n *= 1_000
    elif s == "M":
        n *= 1_000_000
    elif s in ("B", "G"):
        n *= 1_000_000_000
    return n


def _extract_cited_numbers(text: str) -> list[float]:
    nums: list[float] = []
    for m in _CITED_NUM_RE.finditer(text):
        n = _parse_cited_number(m.group(1), m.group(2) or "")
        if n is not None:
            nums.append(n)
    return nums


def _build_allowed_number_set(facts: list[QueryFacts]) -> set[float]:
    """Universe of real numbers from verified DataFacts."""
    allowed: set[float] = set()
    for qf in facts:
        allowed.add(float(qf.rows_returned))
        for cf in qf.columns.values():
            if cf.col_type == "numeric":
                for attr in ("min_val", "max_val", "sum_val", "avg_val"):
                    v = getattr(cf, attr)
                    if v is not None:
                        allowed.add(float(v))
                        if v >= 1000:
                            # Add both the exact K-decimal and the floor/ceil
                            # so "171K" (=171000) matches 171258 via tolerance,
                            # and also matches directly if it rounds cleanly.
                            allowed.add(round(v / 1000, 1))
                            allowed.add(float(int(v / 1000)))       # floor K
                            allowed.add(float(int(v / 1000) + 1))   # ceil K
                        if v >= 1_000_000:
                            allowed.add(round(v / 1_000_000, 1))
                            allowed.add(float(int(v / 1_000_000)))
                            allowed.add(float(int(v / 1_000_000) + 1))
                for v in cf.all_unique_values:
                    allowed.add(float(v))
                    if v != 0:
                        allowed.add(round(v * 100, 2))
                if cf.row_count:
                    allowed.add(float(cf.row_count))
            elif cf.col_type == "categorical":
                if cf.unique_count is not None:
                    allowed.add(float(cf.unique_count))
                if cf.row_count:
                    allowed.add(float(cf.row_count))
                for cnt in cf.top_values.values():
                    allowed.add(float(cnt))
                    if cf.row_count and cf.row_count > 0:
                        allowed.add(round(cnt / cf.row_count * 100, 2))
    return allowed


def _tolerance_match(cited: float, allowed: set[float], tol: float = 0.02) -> bool:
    if cited in allowed:
        return True
    for a in allowed:
        if a != 0 and abs(cited - a) / max(abs(a), 1.0) <= tol:
            return True
    return False


def _programmatic_hallucination_check(
    insight: InsightReport,
    facts: list[QueryFacts],
) -> list[Issue]:
    all_text = " ".join([
        insight.headline,
        insight.executive_summary,
        *insight.key_findings,
        *insight.anomalies,
        *insight.recommendations,
    ])

    cited = _extract_cited_numbers(all_text)
    if not cited:
        return []

    allowed = _build_allowed_number_set(facts)
    issues: list[Issue] = []

    for n in cited:
        # Skip small safe ordinals (top 3, 2 categories, etc.)
        if n <= 10 and n == int(n):
            continue
        # Skip year-like integers — temporal references (2024, 2026, …),
        # not data values; the regex will extract them from "April 2026" etc.
        if 1800 <= n <= 2200 and n == int(n):
            continue
        if not _tolerance_match(n, allowed):
            issues.append(Issue(
                severity="error",
                category="hallucination",
                message=(
                    f"The insight cites {n:g} which cannot be traced to the verified "
                    f"data. Remove it or replace with an actual data value."
                ),
                location="insight",
            ))

    # Deduplicate
    seen: set[str] = set()
    unique: list[Issue] = []
    for i in issues:
        key = i.message[:60]
        if key not in seen:
            seen.add(key)
            unique.append(i)
    return unique


# ---------------------------------------------------------------------------
# Layer 2 — LLM semantic review
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a semantic quality reviewer for a data-analytics report.

IMPORTANT: A PROGRAMMATIC NUMBER CHECK has already verified every cited number
against real DataFacts with K/M/% normalisation and 2% tolerance.
Numbers marked ✓ are CONFIRMED CORRECT — do NOT flag them, ever.
Only numbers marked ✗ may be flagged as potential hallucinations.

YOUR SCOPE — semantic issues only (what a program cannot judge):

1. MIS-LABEL (warning)
   A visual's title or unit clearly misrepresents the underlying column.
   Example: title says "Revenue" but the column is named "transaction_count".

2. UNADDRESSED (warning)
   The plan provably missed a material part of the user's question.
   Example: user asked "by region" but no visual shows geographic breakdown.

3. DIRECTION ERROR (error)
   Insight says a metric "increased" or "improved" but the data trend is flat
   or decreasing. Only flag when clearly contradicted by the verified facts.

4. OTHER (info)
   Minor wording issue, vague recommendation, grammatical error.

DO NOT CHECK (handled programmatically — your output is ignored for these):
  ✗ Whether cited numbers match the data  (→ see PROGRAMMATIC NUMBER CHECK)
  ✗ K/M/% rounding acceptability           (→ already cleared programmatically)
  ✗ Year references (2024, 2026, etc.)     (→ temporal context, not data values)
  ✗ Empty-result misrepresentation         (→ caught by Layer 1)

SCORING:
  passed=false ONLY when at least one issue has severity="error".
  If you find no semantic issues: passed=true, score=1.0, issues=[].
"""

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


def _visuals_summary(plan: AnalysisPlan) -> str:
    return "\n".join(
        f"  [{v.id}] type={v.type} from={v.from_query} "
        f"title={v.title!r} x={v.x_col!r} y={v.y_col!r} unit={v.unit!r}"
        for v in plan.visuals
    )


# ---------------------------------------------------------------------------
# Internal model for LLM structured output
# ---------------------------------------------------------------------------

class _CriticLLMOutput(BaseModel):
    """Internal model — what the LLM fills in. Converted to CritiqueReport afterward."""

    passed: bool = Field(
        description=(
            "True only if NO error-severity issues were found. "
            "Set to false if any issue has severity='error'. "
            "WARNING and INFO issues alone do NOT set passed=false."
        )
    )
    score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Quality score 0.0–1.0. Start at 1.0 and subtract: "
            "0.3 per error, 0.1 per warning, 0.02 per info. "
            "Never go below 0.0."
        ),
    )
    issues: list[Issue] = Field(
        default_factory=list,
        description=(
            "List of semantic issues found. Empty list if everything is correct. "
            "Do NOT include issues for numbers that were marked ✓ in the "
            "PROGRAMMATIC NUMBER CHECK section — those are already verified."
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _programmatic_empty_check(
    insight: InsightReport,
    facts: list[QueryFacts],
) -> list[Issue]:
    """
    Programmatically detect when the insight makes specific claims about
    queries that returned zero rows.  The LLM is unreliable for this check
    because it requires cross-referencing DataFacts, not semantic judgement.
    """
    empty_queries = {qf.query_id: qf.purpose for qf in facts if qf.rows_returned == 0}
    if not empty_queries:
        return []

    # Build the full insight text for scanning
    all_text = " ".join([
        insight.headline,
        insight.executive_summary,
        *insight.key_findings,
        *insight.anomalies,
        *insight.recommendations,
    ]).lower()

    issues: list[Issue] = []
    for qid, purpose in empty_queries.items():
        # Heuristic: if numbers appear in the insight alongside words from the
        # empty query's purpose, the insight is probably inventing data.
        purpose_words = [w for w in purpose.lower().split() if len(w) > 3]
        purpose_hit = sum(1 for w in purpose_words if w in all_text)
        if purpose_hit >= 2 and _CITED_NUM_RE.search(all_text):
            issues.append(Issue(
                severity="warning",
                category="empty",
                message=(
                    f"Query {qid} (\"{purpose}\") returned 0 rows, but the insight "
                    f"appears to cite numeric findings for this topic. "
                    f"State 'No data available' instead."
                ),
                location="insight",
            ))
    return issues


def _build_verified_numbers_block(
    cited: list[float],
    allowed: set[float],
) -> str:
    """
    Summarise which cited numbers passed the programmatic check.
    Injected into the LLM critic's context so it cannot override the
    deterministic result — it only reviews what programs can't judge.
    """
    if not cited:
        return "(no numbers cited in the insight)"
    lines: list[str] = []
    for n in cited:
        if 1800 <= n <= 2200 and n == int(n):
            lines.append(f"  {n:g}  ✓ skipped (year / temporal reference)")
            continue
        if n <= 10 and n == int(n):
            lines.append(f"  {n:g}  ✓ skipped (small ordinal)")
            continue
        status = "✓ verified" if _tolerance_match(n, allowed) else "✗ NOT FOUND in data"
        lines.append(f"  {n:g}  {status}")
    return "\n".join(lines)


async def critique(
    question: str,
    intent: Intent,
    plan: AnalysisPlan,
    results: dict[str, QueryResult],
    insight: InsightReport,
    data_facts: Optional[list[QueryFacts]] = None,
) -> CritiqueReport:
    """
    Two-layer critic.

    Layer 1 (programmatic, deterministic, zero tokens):
      • Number hallucination check — extracts cited numbers, cross-references
        against DataFacts with K/M/% normalisation and 2% tolerance
      • Empty-result misrepresentation — detects claims about 0-row queries

    Layer 2 (LLM, semantic):
      • Receives the Layer 1 verdict injected into its context so it CANNOT
        re-check numbers that were already verified — it focuses only on
        semantic issues: label accuracy, unaddressed question parts, logic.
    """
    t0 = time.perf_counter()

    from app.agents.insight_agent import compute_data_facts
    facts = data_facts if data_facts is not None else compute_data_facts(plan, results)

    # -----------------------------------------------------------------------
    # Layer 1 — deterministic checks (no LLM cost)
    # -----------------------------------------------------------------------
    prog_issues = _programmatic_hallucination_check(insight, facts)
    prog_issues += _programmatic_empty_check(insight, facts)

    # Build the "verified numbers" block to hand to the LLM critic
    all_text = " ".join([
        insight.headline, insight.executive_summary,
        *insight.key_findings, *insight.anomalies, *insight.recommendations,
    ])
    cited_nums = _extract_cited_numbers(all_text)
    allowed_nums = _build_allowed_number_set(facts)
    verified_block = _build_verified_numbers_block(cited_nums, allowed_nums)

    # -----------------------------------------------------------------------
    # Layer 2 — LLM semantic review
    # -----------------------------------------------------------------------
    facts_block = _facts_to_prompt_block(facts)
    insight_text = (
        f"Headline: {insight.headline}\n"
        f"Summary: {insight.executive_summary}\n"
        f"Key findings:\n" + "\n".join(f"  - {x}" for x in insight.key_findings) + "\n"
        f"Anomalies:\n" + "\n".join(f"  - {x}" for x in insight.anomalies) + "\n"
        f"Recommendations:\n" + "\n".join(f"  - {x}" for x in insight.recommendations)
    )

    user_msg = (
        f"## User question\n\n{question}\n\n"
        f"## Plan\n\nTitle: {plan.title}\n\n"
        f"### Visuals\n{_visuals_summary(plan)}\n\n"
        f"## VERIFIED DATA FACTS\n\n{facts_block}\n\n"
        f"## PROGRAMMATIC NUMBER CHECK (authoritative — do NOT re-check these)\n"
        f"The following numbers were extracted from the insight and verified against\n"
        f"the data programmatically. Accept ✓ results as correct; do not flag them.\n"
        f"Only numbers marked ✗ are potential hallucinations.\n\n"
        f"{verified_block}\n\n"
        f"## Insight narrative\n\n{insight_text}\n"
    )

    llm = llm_for("critic")
    structured_llm = llm.with_structured_output(_CriticLLMOutput, include_raw=True)

    llm_issues: list[Issue] = []
    llm_score = 1.0
    llm_passed = True

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(result["raw"])

        if result["parsed"] is not None and result["parsing_error"] is None:
            output: _CriticLLMOutput = result["parsed"]
            llm_issues = output.issues
            llm_score = output.score
            llm_passed = output.passed
        else:
            # Fallback: lenient JSON parse on raw text when structured output fails
            raw_text = str(result["raw"].content)
            parsed = _parse_json_lenient(raw_text)
            if parsed:
                for ri in (parsed.get("issues") or []):
                    try:
                        llm_issues.append(Issue.model_validate(ri))
                    except Exception:
                        pass
                llm_score = float(parsed.get("score", 1.0))
                llm_passed = bool(parsed.get("passed", True))
    except Exception as exc:
        llm_issues.append(Issue(
            severity="info", category="other",
            message=f"Critic LLM unavailable ({exc.__class__.__name__}); programmatic check only.",
        ))

    all_issues = prog_issues + llm_issues
    has_error = any(i.severity == "error" for i in all_issues)
    prog_penalty = len(prog_issues) * 0.2
    final_score = max(0.0, min(1.0, llm_score - prog_penalty))

    return CritiqueReport(
        passed=not has_error and llm_passed,
        score=final_score,
        issues=all_issues,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
