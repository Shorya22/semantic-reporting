"""
Orchestrator — runs the multi-agent pipeline and emits SSE-shaped events.

Pipeline (per question)
-----------------------
1.  classify_intent          → emits ``intent``
2.  trivial / clarify branch → emit ``token`` + ``done``  (no SQL at all)
3.  get_schema_context       → cached; informs greeting/help replies
4.  plan_analysis            → emits ``plan``
5.  run queries in parallel  → emits ``query_start`` + ``query_done`` per query
    re-plan trigger          → if >50 % queries fail, regenerate plan and re-run
6.  design_all_visuals       → emits ``viz`` per visual + ``dashboard_layout``
                               + back-compat ``chart_spec`` / ``table_data``
7.  generate_insights        → emits ``insight``
    DataFacts computed ONCE  → passed to insight_agent AND critic (no double work)
8.  critique feedback loop   → emits ``critique``; regenerates insight on error issues
                               (capped at _MAX_INSIGHT_RETRIES iterations)
9.  usage + done             → emits ``usage`` + ``done``

Each event is a ``dict`` ready to be sent over SSE (the route handler wraps
it in ``data: {json}\\n\\n``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Optional

from langchain_community.utilities import SQLDatabase

from app.agents._usage import start_bucket, totals as usage_totals
from app.agents.critic import critique
from app.agents.insight_agent import (
    InsightReport,
    QueryFacts,
    compute_data_facts,
    generate_insights,
)
from app.agents.intent_classifier import Intent, classify_intent
from app.agents.planner import AnalysisPlan, plan_analysis
from app.agents.schema_agent import SchemaContext, get_schema_context
from app.agents.sql_workers import QueryResult, run_one_query
from app.agents.viz_designer import RenderedVisual, design_visual


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Re-plan when this fraction of queries fail on the first attempt.
_REPLAN_FAILURE_THRESHOLD = 0.50

# Max critic-driven insight regeneration attempts.
_MAX_INSIGHT_RETRIES = 2

# Intent confidence below this threshold triggers a clarification request.
_CLARIFICATION_THRESHOLD = 0.35


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def _evt(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, **fields}


def _layout_dump(plan: AnalysisPlan) -> list[dict[str, Any]]:
    return [
        {"slots": [{"visual_id": s.visual_id, "width": s.width} for s in row.slots]}
        for row in plan.layout
    ]


# ---------------------------------------------------------------------------
# Dynamic trivial-intent replies (use schema context, not hardcoded strings)
# ---------------------------------------------------------------------------

def _greeting_reply(schema: Optional[SchemaContext] = None) -> str:
    if schema and schema.profiles:
        names = list(schema.profiles.keys())[:4]
        table_list = ", ".join(f"*{n}*" for n in names)
        tail = " and more" if len(schema.profiles) > 4 else ""
        context = f" I have access to tables like {table_list}{tail}."
    else:
        context = ""
    return (
        f"Hello! I can help you query and visualize this database in plain English.{context}\n\n"
        "Try asking things like *\"Show me the top 10 by transaction count\"* or "
        "*\"Give me a complete performance overview\"*."
    )


def _help_reply(schema: Optional[SchemaContext] = None) -> str:
    body = (
        "I'm a multi-agent SQL analyst. You can:\n\n"
        "• Ask data questions in plain English (\"How many transactions today?\")\n"
        "• Request charts implicitly (\"Show monthly trends\")\n"
        "• Ask for full dashboards (\"Performance overview of all AUAs\")\n"
        "• Generate exports (\"Generate a PDF report on KYC activity\")\n\n"
        "**I operate in read-only mode** — I can only SELECT data, never modify it."
    )
    if schema and schema.summary:
        body += f"\n\n**Connected database:** {schema.summary}"
    return body


async def _emit_trivial(reply: str) -> AsyncGenerator[dict[str, Any], None]:
    """Stream a reply word-by-word so the frontend can animate it."""
    for word in reply.split(" "):
        yield _evt("token", content=word + " ")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Parallel SQL execution helper — yields (event, result) pairs in completion
# order so the caller gets real-time progress without buffering.
# ---------------------------------------------------------------------------

async def _execute_plan_queries(
    plan: AnalysisPlan,
    db: SQLDatabase,
    session_id: str,
    schema_ddl: str,
) -> AsyncGenerator[tuple[dict[str, Any], QueryResult], None]:
    """
    Run all PlannedQuery tasks concurrently. Yields ``(query_done_event, result)``
    tuples as each finishes so the orchestrator can stream them immediately.
    """
    pending: dict[str, asyncio.Task[QueryResult]] = {
        q.id: asyncio.create_task(run_one_query(q, db, session_id, schema_ddl))
        for q in plan.queries
    }
    while pending:
        done, _ = await asyncio.wait(pending.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            event = _evt(
                "query_done",
                query_id=result.query_id,
                success=result.success,
                rows_count=result.rows_count,
                latency_ms=result.latency_ms,
                repaired=result.repaired,
                repair_strategy=result.repair_strategy,
                error=result.error,
            )
            yield event, result
            for qid, t in list(pending.items()):
                if t is task:
                    del pending[qid]
                    break


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_orchestrator(
    *,
    question: str,
    db: SQLDatabase,
    session_id: str,
    schema_ddl_hint: Optional[str] = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Full multi-agent pipeline. Yields SSE-shaped event dicts.

    Parameters
    ----------
    question         : user's natural-language question
    db               : connected SQLDatabase (LangChain wrapper)
    session_id       : used for caching + per-session state
    schema_ddl_hint  : optional pre-fetched DDL (skips schema agent call)
    """
    pipeline_t0 = time.perf_counter()
    start_bucket()  # reset per-pipeline token accumulator

    # -----------------------------------------------------------------------
    # 1. Intent classification
    # -----------------------------------------------------------------------
    intent: Intent = await classify_intent(question)
    yield _evt("intent", **intent.model_dump())

    # -----------------------------------------------------------------------
    # 2. Schema — fetch early so greeting/help can use table names
    # -----------------------------------------------------------------------
    schema: SchemaContext = await get_schema_context(session_id, db)

    # Trivial branch: greeting
    if intent.intent == "greeting":
        async for e in _emit_trivial(_greeting_reply(schema)):
            yield e
        yield _evt("done", elapsed_ms=int((time.perf_counter() - pipeline_t0) * 1000))
        return

    # Trivial branch: help
    if intent.intent == "help":
        async for e in _emit_trivial(_help_reply(schema)):
            yield e
        yield _evt("done", elapsed_ms=int((time.perf_counter() - pipeline_t0) * 1000))
        return

    # Low-confidence: ask for clarification rather than guessing
    if intent.confidence < _CLARIFICATION_THRESHOLD:
        clarify = (
            "I'm not sure I understood your question correctly. "
            "Could you clarify what you'd like to see? For example:\n\n"
            "• Specify the metric or dimension (e.g. \"by month\", \"per AUA\")\n"
            "• Mention the time range (e.g. \"last 7 days\", \"Q1 2024\")\n"
            "• Say if you want a chart, a table, or just a single number\n\n"
            f"Your question: *\"{question}\"*"
        )
        async for e in _emit_trivial(clarify):
            yield e
        yield _evt("done", elapsed_ms=int((time.perf_counter() - pipeline_t0) * 1000))
        return

    # -----------------------------------------------------------------------
    # 3. Plan
    # -----------------------------------------------------------------------
    plan: AnalysisPlan = await plan_analysis(question, intent, schema.ddl)
    yield _evt(
        "plan",
        title=plan.title,
        description=plan.description,
        query_count=len(plan.queries),
        visual_count=len(plan.visuals),
        layout=_layout_dump(plan),
        latency_ms=plan.latency_ms,
    )

    if not plan.queries:
        msg = (
            "I couldn't break this question into any executable SQL queries "
            "against the connected database. Try rephrasing or asking about "
            "a specific table."
        )
        for word in msg.split(" "):
            yield _evt("token", content=word + " ")
        yield _evt("done", elapsed_ms=int((time.perf_counter() - pipeline_t0) * 1000))
        return

    # -----------------------------------------------------------------------
    # 4. SQL Workers — emit start events then run in parallel
    # -----------------------------------------------------------------------
    for q in plan.queries:
        yield _evt("query_start", query_id=q.id, purpose=q.purpose)

    results: dict[str, QueryResult] = {}
    async for ev, result in _execute_plan_queries(plan, db, session_id, schema.ddl):
        results[result.query_id] = result
        yield ev

    # -----------------------------------------------------------------------
    # 4b. Re-plan when too many queries failed
    #
    # If more than _REPLAN_FAILURE_THRESHOLD of queries fail, re-call the
    # Planner with the error context so it can avoid the same mistakes.
    # We only adopt the re-plan when it demonstrably reduces failures.
    # -----------------------------------------------------------------------
    total_q = len(plan.queries)
    failed_q = sum(1 for r in results.values() if not r.success)

    if total_q > 0 and failed_q / total_q > _REPLAN_FAILURE_THRESHOLD:
        error_ctx = "\n".join(
            f"  [{qid}] {r.error[:300]}"
            for qid, r in results.items()
            if not r.success and r.error
        )
        # Inject failure context into the question so the Planner rewrites queries
        replan_question = (
            f"{question}\n\n"
            f"[REPLAN: previous attempt had {failed_q}/{total_q} query failures —\n"
            f"{error_ctx}\n"
            f"Write alternative queries that avoid these errors using only the schema provided.]"
        )

        plan2: AnalysisPlan = await plan_analysis(replan_question, intent, schema.ddl)

        if plan2.queries:
            yield _evt(
                "plan",
                title=plan2.title,
                description=plan2.description,
                query_count=len(plan2.queries),
                visual_count=len(plan2.visuals),
                layout=_layout_dump(plan2),
                latency_ms=plan2.latency_ms,
                replan=True,
            )

            for q in plan2.queries:
                yield _evt("query_start", query_id=q.id, purpose=q.purpose)

            results2: dict[str, QueryResult] = {}
            async for ev, result in _execute_plan_queries(plan2, db, session_id, schema.ddl):
                results2[result.query_id] = result
                yield ev

            new_failed = sum(1 for r in results2.values() if not r.success)
            if new_failed < failed_q:
                # Re-plan reduced errors — adopt it
                plan = plan2
                results = results2

    # -----------------------------------------------------------------------
    # 5. Visuals (deterministic renderer; skips failed query sources)
    # -----------------------------------------------------------------------
    rendered: list[RenderedVisual] = []
    for v in plan.visuals:
        result = results.get(v.from_query)
        if result is None or not result.success:
            continue
        rv = design_visual(v, result)
        rendered.append(rv)

        yield _evt(
            "viz",
            visual_id=rv.visual_id,
            visual_type=rv.type,
            title=rv.title,
            subtitle=rv.subtitle,
            from_query=rv.from_query,
            kpi=(rv.kpi.model_dump() if rv.kpi else None),
            echarts_option=rv.echarts_option,
            table_columns=rv.table_columns,
            table_rows=rv.table_rows,
            rows_count=rv.rows_count,
            error=rv.error,
        )

        # Back-compat events for the existing chat-style UI
        if rv.echarts_option:
            yield _evt(
                "chart_spec",
                id=rv.visual_id,
                option=rv.echarts_option,
                title=rv.title,
                sql=rv.sql,
            )
        if rv.table_rows:
            yield _evt(
                "table_data",
                id=rv.visual_id,
                columns=rv.table_columns,
                rows=rv.table_rows,
                sql=rv.sql,
                title=rv.title,
            )

    yield _evt(
        "dashboard_layout",
        title=plan.title,
        layout=_layout_dump(plan),
        visuals=[v.model_dump() for v in rendered],
    )

    # -----------------------------------------------------------------------
    # 6. Insight narrative  +  7. Critic feedback loop
    #
    # DataFacts are computed ONCE from ALL rows of ALL successful queries.
    # The same ``facts`` object is passed to both generate_insights() and
    # critique() on every iteration — no redundant computation.
    # -----------------------------------------------------------------------
    facts: list[QueryFacts] = compute_data_facts(plan, results)

    insight: InsightReport = await generate_insights(
        question, intent, plan, results, data_facts=facts
    )
    yield _evt("insight", **insight.model_dump())

    # Stream executive summary as tokens for the chat-style UI.
    # Only on the first generation — retries re-emit the structured ``insight``
    # event which the frontend updates in place.
    for word in (insight.executive_summary or "").split(" "):
        yield _evt("token", content=word + " ")

    final_critique = None
    for attempt in range(_MAX_INSIGHT_RETRIES + 1):
        try:
            report = await critique(
                question, intent, plan, results, insight, data_facts=facts
            )
        except Exception:
            break  # critic unavailable — accept current insight

        error_issues = [i for i in report.issues if i.severity == "error"]

        if report.passed or not error_issues or attempt == _MAX_INSIGHT_RETRIES:
            final_critique = report
            break

        # Re-generate with critic error feedback so the LLM corrects itself
        insight = await generate_insights(
            question, intent, plan, results,
            critique_feedback=error_issues,
            data_facts=facts,
        )
        yield _evt("insight", **insight.model_dump())

    if final_critique is not None:
        yield _evt("critique", **final_critique.model_dump())

    # -----------------------------------------------------------------------
    # 8. export_ctx for CSV / Excel / PDF download buttons
    # -----------------------------------------------------------------------
    last_sql = next(
        (r.sql for r in reversed(list(results.values())) if r.success and r.sql),
        None,
    )
    if last_sql:
        yield _evt("export_ctx", sql=last_sql, session_id=session_id)

    # -----------------------------------------------------------------------
    # 9. Final usage + done
    # -----------------------------------------------------------------------
    elapsed_ms = int((time.perf_counter() - pipeline_t0) * 1000)
    in_tok, out_tok = usage_totals()
    yield _evt(
        "usage",
        intent_latency_ms=intent.latency_ms,
        plan_latency_ms=plan.latency_ms,
        insight_latency_ms=insight.latency_ms,
        total_elapsed_ms=elapsed_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        total_tokens=in_tok + out_tok,
        latency_ms=elapsed_ms,
    )
    yield _evt("done", elapsed_ms=elapsed_ms)
