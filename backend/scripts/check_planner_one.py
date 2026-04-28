"""Single-question planner test (avoids hitting rate limits)."""
import asyncio
from langchain_community.utilities import SQLDatabase
from app.agents.intent_classifier import classify_intent
from app.agents.planner import plan_analysis


async def go():
    db = SQLDatabase.from_uri("postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo")
    schema = db.get_table_info()
    q = "Give me a complete performance overview of all AUAs"
    intent = await classify_intent(q)
    print(f"Intent ({intent.latency_ms}ms): {intent.intent}, complexity={intent.complexity}, "
          f"dashboard={intent.wants_dashboard}, chart={intent.wants_chart}")
    plan = await plan_analysis(q, intent, schema)
    print(f"\nPlan ({plan.latency_ms}ms): {plan.title}")
    print(f"  description: {plan.description}")
    print(f"  queries: {len(plan.queries)}")
    for qq in plan.queries:
        print(f"    [{qq.id}] {qq.purpose}")
        sql_short = qq.sql[:240].replace("\n", " ").strip()
        print(f"          SQL: {sql_short}{'...' if len(qq.sql) > 240 else ''}")
        print(f"          cols: {qq.expected_columns}")
    print(f"  visuals: {len(plan.visuals)}")
    for v in plan.visuals:
        print(f"    [{v.id}] {v.type:<14s} from {v.from_query}: {v.title}  (x={v.x_col}, y={v.y_col})")
    print(f"  layout: {len(plan.layout)} rows")
    for ri, row in enumerate(plan.layout, start=1):
        slots = " | ".join(f"{s.visual_id}({s.width})" for s in row.slots)
        print(f"    row {ri}: {slots}")


if __name__ == "__main__":
    asyncio.run(go())
