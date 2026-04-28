"""End-to-end test: question -> intent -> plan -> SQL -> rendered visuals."""
import asyncio
import json

from langchain_community.utilities import SQLDatabase

from app.agents.intent_classifier import classify_intent
from app.agents.planner import plan_analysis
from app.agents.schema_agent import get_schema_context
from app.agents.sql_workers import run_planned_queries
from app.agents.viz_designer import design_all_visuals


PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


async def go() -> None:
    db = SQLDatabase.from_uri(PG_URI)
    schema = await get_schema_context("test", db)

    question = "Give me a complete performance overview of all AUAs"
    intent = await classify_intent(question)
    plan = await plan_analysis(question, intent, schema.ddl)
    results = await run_planned_queries(plan, db, schema, session_id="test")
    rendered = design_all_visuals(plan.visuals, results)

    print(f"Question: {question!r}")
    print(f"\nRendered {len(rendered)} visuals:\n")
    for r in rendered:
        print("-" * 80)
        print(f"  [{r.visual_id}] {r.type:<14s}  {r.title}")
        if r.error:
            print(f"    ERROR: {r.error}")
            continue
        if r.kpi:
            print(f"    KPI:   value={r.kpi.value!r}  formatted={r.kpi.formatted_value!r}  unit={r.kpi.unit!r}")
        if r.echarts_option:
            opt = r.echarts_option
            series = opt.get("series", [])
            stype = series[0].get("type") if series else "?"
            x_count = len(opt.get("xAxis", {}).get("data", [])) if isinstance(opt.get("xAxis"), dict) else "n/a"
            print(f"    chart: series_type={stype}  x_data_count={x_count}")
            print(f"    title: {opt.get('title', {}).get('text', '?')}")
        if r.table_rows:
            print(f"    table: {len(r.table_rows)} rows x {len(r.table_columns)} cols")
        print(f"    rows: {r.rows_count}")


if __name__ == "__main__":
    asyncio.run(go())
