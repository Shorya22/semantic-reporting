"""End-to-end smoke test: question -> intent -> plan -> parallel SQL workers."""
import asyncio
import time

from langchain_community.utilities import SQLDatabase

from app.agents.intent_classifier import classify_intent
from app.agents.planner import plan_analysis
from app.agents.schema_agent import get_schema_context
from app.agents.sql_workers import run_planned_queries


PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


async def go() -> None:
    db = SQLDatabase.from_uri(PG_URI)

    print("Stage 0: Schema context (cached after first run)...")
    t0 = time.perf_counter()
    schema = await get_schema_context("test", db)
    print(f"  {(time.perf_counter() - t0) * 1000:,.0f} ms\n")

    question = "Give me a complete performance overview of all AUAs"
    print(f"Question: {question!r}\n")

    print("Stage 1: Intent classification...")
    t0 = time.perf_counter()
    intent = await classify_intent(question)
    print(f"  {(time.perf_counter() - t0) * 1000:,.0f} ms  -> "
          f"{intent.intent}, complexity={intent.complexity}, dashboard={intent.wants_dashboard}\n")

    print("Stage 2: Planning...")
    t0 = time.perf_counter()
    plan = await plan_analysis(question, intent, schema.ddl)
    print(f"  {(time.perf_counter() - t0) * 1000:,.0f} ms  -> "
          f"{len(plan.queries)} queries, {len(plan.visuals)} visuals\n")

    if not plan.queries:
        print("  (Planner returned no queries; skipping execution.)")
        return

    print("Stage 3: Parallel SQL execution...")
    t0 = time.perf_counter()
    results = await run_planned_queries(plan, db, schema, session_id="test")
    total = (time.perf_counter() - t0) * 1000
    print(f"  Total wall-clock: {total:,.0f} ms (parallel)\n")

    print("Per-query results:")
    print("-" * 100)
    print(f"{'qid':<5}{'success':<8}{'latency':>8}  {'rows':>5}  {'cols':<60}  notes")
    print("-" * 100)
    for qid, r in results.items():
        cols = ", ".join(r.columns)[:58]
        flag = "OK" if r.success else "FAIL"
        notes = ""
        if r.repaired:
            notes = "(REPAIRED)"
        if not r.success:
            notes = (r.error or "")[:60]
        print(f"{qid:<5}{flag:<8}{r.latency_ms:>6}ms  {r.rows_count:>5}  {cols:<60}  {notes}")

    # Show first 3 rows of each successful query for sanity
    print("\nSample data (first 3 rows per query):")
    for qid, r in results.items():
        if not r.success or not r.rows:
            continue
        print(f"\n  [{qid}] columns: {r.columns}")
        for row in r.rows[:3]:
            print(f"        {row}")


if __name__ == "__main__":
    asyncio.run(go())
