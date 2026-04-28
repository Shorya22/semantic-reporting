"""End-to-end test: question -> intent -> plan -> SQL -> visuals -> insights."""
import asyncio
import time

from langchain_community.utilities import SQLDatabase

from app.agents.intent_classifier import classify_intent
from app.agents.insight_agent import generate_insights
from app.agents.planner import plan_analysis
from app.agents.schema_agent import get_schema_context
from app.agents.sql_workers import run_planned_queries


PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


async def go() -> None:
    db = SQLDatabase.from_uri(PG_URI)
    schema = await get_schema_context("test", db)

    question = "Give me a complete performance overview of all AUAs"
    print(f"Question: {question!r}\n")

    intent = await classify_intent(question)
    plan = await plan_analysis(question, intent, schema.ddl)
    results = await run_planned_queries(plan, db, schema, session_id="test")

    print(f"Plan: {len(plan.queries)} queries -> {sum(1 for r in results.values() if r.success)} succeeded\n")

    print("Calling Insight Agent ...")
    t0 = time.perf_counter()
    report = await generate_insights(question, intent, plan, results)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  ({elapsed:,.0f} ms)\n")

    print("=" * 80)
    print("HEADLINE")
    print("=" * 80)
    print(f"  {report.headline}")
    print()
    print("EXECUTIVE SUMMARY")
    print("=" * 80)
    print(f"  {report.executive_summary}")
    print()
    print("KEY FINDINGS")
    print("=" * 80)
    for i, f in enumerate(report.key_findings, start=1):
        print(f"  {i}. {f}")
    print()
    print("ANOMALIES")
    print("=" * 80)
    for i, a in enumerate(report.anomalies, start=1):
        print(f"  {i}. {a}")
    if not report.anomalies:
        print("  (none)")
    print()
    print("RECOMMENDATIONS")
    print("=" * 80)
    for i, r in enumerate(report.recommendations, start=1):
        print(f"  {i}. {r}")
    if not report.recommendations:
        print("  (none)")


if __name__ == "__main__":
    asyncio.run(go())
