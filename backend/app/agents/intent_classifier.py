"""
Intent Classifier — first agent in the multi-agent pipeline.

Reads the user's question and produces a structured ``Intent`` describing
what the orchestrator should do next: greet, answer a single fact, render
charts, build a full dashboard, or generate an exportable report.

Why this exists
---------------
Today's pipeline routes every question through the same expensive ReAct
agent. With an Intent Classifier we can:

* short-circuit greetings/help in < 100 ms (no LLM at all);
* skip chart generation for pure-fact questions ("how many rows?");
* trigger the full Planner → SQL Workers → Viz → Insight chain only when
  the user genuinely wants a dashboard / multi-chart analysis;
* produce an export automatically when the question implies it
  ("send me a pdf report on …").

Latency budget: ≤ 500 ms (uses the smallest Groq model — typically
``llama-3.1-8b-instant``).
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agents.llm_factory import llm_for


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

IntentLabel = Literal[
    "greeting",       # "Hi", "Hello"
    "help",           # "What can you do?", "How does this work?"
    "simple_qa",      # "How many rows in transactions?"
    "metric",         # "What's the success rate?"  (single KPI)
    "exploration",    # "Show me trends" / "Let me explore"
    "dashboard",      # explicit dashboard / overview request
    "report",         # "Generate a report on …"
    "comparison",     # "Compare X vs Y"
]

ChartHint = Literal[
    "trend", "comparison", "proportion", "ranking",
    "distribution", "geographic", "correlation",
]

ExportFormat = Literal["pdf", "excel", "csv"]


class Intent(BaseModel):
    """Structured classification of one user question."""

    intent: IntentLabel = Field(
        description=(
            "The high-level intent category this question maps to. "
            "Must be one of: greeting, help, simple_qa, metric, "
            "exploration, dashboard, report, comparison."
        )
    )
    wants_chart: bool = Field(
        default=False,
        description=(
            "True when the question implies any visualization "
            "(trend, ranking, chart, graph, visualization)."
        ),
    )
    wants_dashboard: bool = Field(
        default=False,
        description=(
            "True when multiple charts in a grid are needed "
            "(overview, dashboard, performance summary)."
        ),
    )
    wants_export: Optional[ExportFormat] = Field(
        default=None,
        description=(
            "null unless the user explicitly asks for PDF, Excel, or CSV download."
        ),
    )
    chart_hints: list[ChartHint] = Field(
        default_factory=list,
        description=(
            "Zero or more applicable viz patterns; empty list if none apply."
        ),
    )
    time_window: Optional[str] = Field(
        default=None,
        description=(
            "Exact phrase extracted from question, e.g. 'last 7 days' or "
            "'Q1 2024'; null if no time window."
        ),
    )
    complexity: Literal["simple", "moderate", "complex"] = Field(
        default="simple",
        description=(
            "'simple' for single metric queries; 'moderate' for 2-3 chart "
            "questions; 'complex' for full dashboards."
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Key domain nouns (lowercase): table names, column concepts, "
            "metric names, filter values."
        ),
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description=(
            "Your confidence in this classification 0.0–1.0; "
            "use 0.9+ only when unambiguous."
        ),
    )
    latency_ms: int = Field(
        default=0,
        description="How long classification took (instrumentation).",
    )


# ---------------------------------------------------------------------------
# Deterministic short-circuits — avoid an LLM round-trip when possible
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = {
    "hi", "hello", "hey", "hii", "hiii", "hola", "namaste",
    "good morning", "good afternoon", "good evening",
    "yo", "sup", "howdy", "greetings",
}

_HELP_PATTERNS = re.compile(
    r"^\s*(what (can|do) you|how (do|does) (i|this|it) work|help( me)?$|"
    r"what is this|who are you|capabilities)",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|hola|namaste|yo|sup|howdy|greetings|"
    r"good\s+(morning|afternoon|evening))\b[\s!.?,]*$",
    re.IGNORECASE,
)


def _short_circuit(question: str) -> Optional[Intent]:
    """Return an Intent for trivial questions (greetings/help) or None."""
    q = question.strip()
    if not q:
        return None

    # Very short pure greeting
    if len(q) <= 25 and (q.lower() in _GREETING_PATTERNS or _GREETING_RE.match(q)):
        return Intent(
            intent="greeting",
            wants_chart=False,
            wants_dashboard=False,
            complexity="simple",
            confidence=1.0,
            latency_ms=0,
        )

    if _HELP_PATTERNS.match(q):
        return Intent(
            intent="help",
            wants_chart=False,
            wants_dashboard=False,
            complexity="simple",
            confidence=0.95,
            latency_ms=0,
        )

    return None


# ---------------------------------------------------------------------------
# LLM-backed classifier
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an intent classifier for a natural-language SQL analytics platform.
Read ONE user question and classify it. All decisions must be conservative — prefer
lower complexity, fewer chart_hints, and lower confidence when ambiguous.

DECISION RULES
--------------
intent:
  greeting    — "Hi", "Hello", "Good morning"
  help        — "What can you do?", "How does this work?"
  simple_qa   — single fact/count, e.g. "How many users registered today?"
  metric      — single KPI, e.g. "What is the success rate?"
  exploration — open-ended, multi-faceted: "Show me trends", "Performance overview"
  dashboard   — explicit dashboard / multi-chart request, "overview of all AUAs"
  report      — "generate a report", "send me a pdf", "create a summary doc"
  comparison  — "compare X vs Y", "X vs Y", "difference between A and B"

chart_hints (apply only when clearly implied):
  trend        — "over time", "monthly", "daily", "by month/week"
  ranking      — "top N", "highest/lowest", "ranking"
  comparison   — "compare", "vs", "difference"
  distribution — "distribution", "spread", "histogram"
  proportion   — "breakdown", "share", "%", "percentage"
  geographic   — "by state", "by region", "by country"
  correlation  — "correlation", "relationship between"

wants_dashboard — set true when: "overview", "performance", "dashboard", "summary",
  or when user asks multiple sub-questions in one message.

wants_export — set to "pdf" when "report"/"summary doc" mentioned; "excel"/"csv" when
  explicitly asked. null otherwise.

Be accurate and concise.
"""


def _parse_json_lenient(text: str) -> Optional[dict]:
    """Strip markdown fences / preamble and parse JSON; returns None on failure."""
    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` fences if present
    if s.startswith("```"):
        # find first { and last }
        first = s.find("{")
        last = s.rfind("}")
        if first != -1 and last != -1:
            s = s[first:last + 1]
    # If model still wrote extra prose, locate the JSON object boundaries
    if not s.startswith("{"):
        first = s.find("{")
        last = s.rfind("}")
        if first == -1 or last == -1:
            return None
        s = s[first:last + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def classify_intent(question: str) -> Intent:
    """Classify the user's question. Always returns an ``Intent``.

    Falls back to ``intent="exploration"`` with low confidence when the LLM
    output cannot be parsed — the orchestrator can still proceed.
    """
    # Path 1: deterministic short-circuit for trivial inputs
    short = _short_circuit(question)
    if short is not None:
        return short

    # Path 2: LLM classification via structured output
    t0 = time.perf_counter()
    llm = llm_for("intent_classifier")
    structured_llm = llm.with_structured_output(Intent, include_raw=True)

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=question.strip()),
        ])
        from app.agents._usage import record as _record_usage
        _record_usage(result["raw"])

        if result["parsed"] is not None and result["parsing_error"] is None:
            return result["parsed"].model_copy(
                update={"latency_ms": int((time.perf_counter() - t0) * 1000)}
            )

        # Structured parse failed — fall back to raw text JSON parse
        raw_text = str(result["raw"].content)
    except Exception:
        return Intent(
            intent="exploration",
            wants_chart=True,
            confidence=0.3,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    # Fallback: manual lenient JSON parse (handles malformed model output)
    parsed = _parse_json_lenient(raw_text)
    if parsed is None:
        return Intent(
            intent="exploration",
            wants_chart=True,
            confidence=0.3,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    try:
        intent = Intent.model_validate(parsed)
    except ValidationError:
        intent = Intent(
            intent=parsed.get("intent") or "exploration",
            wants_chart=bool(parsed.get("wants_chart", True)),
            wants_dashboard=bool(parsed.get("wants_dashboard", False)),
            wants_export=parsed.get("wants_export"),
            chart_hints=parsed.get("chart_hints") or [],
            time_window=parsed.get("time_window"),
            complexity=parsed.get("complexity") or "moderate",
            keywords=parsed.get("keywords") or [],
            confidence=float(parsed.get("confidence", 0.5)),
        )

    return intent.model_copy(
        update={"latency_ms": int((time.perf_counter() - t0) * 1000)}
    )


def classify_intent_sync(question: str) -> Intent:
    """Sync wrapper — convenient for scripts and tests."""
    import asyncio
    return asyncio.run(classify_intent(question))
