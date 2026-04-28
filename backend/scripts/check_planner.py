"""Smoke test the Planner against the AUA/KUA database."""

import asyncio
import json
import time

from langchain_community.utilities import SQLDatabase

from app.agents.intent_classifier import classify_intent
from app.agents.planner import plan_analysis


PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


QUESTIONS = [
    # Should produce a 1-query, 1-KPI plan
    "How many auth transactions happened today?",
    # Multi-chart exploration
    "Show top 10 AUAs by transaction count",
    # Full dashboard
    "Give me a complete performance overview of all AUAs",
    # Comparison
    "Compare success rate of FINGER vs IRIS auth",
]


async def main() -> None:
    db = SQLDatabase.from_uri(PG_URI)
    schema = db.get_table_info()
    print(f"Schema length: {len(schema)} chars\n")

    for i, q in enumerate(QUESTIONS, start=1):
        print("=" * 100)
        print(f"[{i}/{len(QUESTIONS)}] Question: {q!r}")
        print("=" * 100)

        t0 = time.perf_counter()
        intent = await classify_intent(q)
        print(f"\nIntent ({intent.latency_ms}ms): "
              f"{intent.intent}, complexity={intent.complexity}, "
              f"chart={intent.wants_chart}, dashboard={intent.wants_dashboard}, "
              f"export={intent.wants_export}")

        plan = await plan_analysis(q, intent, schema)
        print(f"\nPlan ({plan.latency_ms}ms): {plan.title}")
        print(f"  description: {plan.description}")
        print(f"  queries:  {len(plan.queries)}")
        for q_ in plan.queries:
            sql_preview = q_.sql.replace("\n", " ").strip()[:120]
            print(f"    [{q_.id}] purpose: {q_.purpose}")
            print(f"          sql: {sql_preview}{'...' if len(q_.sql) > 120 else ''}")
            print(f"          cols: {q_.expected_columns}")
        print(f"  visuals: {len(plan.visuals)}")
        for v in plan.visuals:
            print(f"    [{v.id}] {v.type:<14s} from {v.from_query}: {v.title}")
            print(f"          x={v.x_col!r:<20s} y={v.y_col!r:<20s} unit={v.unit!r}")
        print(f"  layout rows: {len(plan.layout)}")
        for ri, row in enumerate(plan.layout, start=1):
            slots = " | ".join(f"{s.visual_id}({s.width})" for s in row.slots)
            print(f"    row {ri}: {slots}")

        total = int((time.perf_counter() - t0) * 1000)
        print(f"\nTotal: {total}ms")
        print()


if __name__ == "__main__":
    asyncio.run(main())
