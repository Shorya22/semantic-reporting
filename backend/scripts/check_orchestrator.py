"""End-to-end test of the multi-agent orchestrator. Prints every SSE event."""
import asyncio
import json
import time

from langchain_community.utilities import SQLDatabase

from app.agents.orchestrator import run_orchestrator


PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


async def go() -> None:
    db = SQLDatabase.from_uri(PG_URI)
    question = "Give me a complete performance overview of all AUAs"
    print(f"Question: {question!r}\n")

    t0 = time.perf_counter()
    counts: dict[str, int] = {}
    async for event in run_orchestrator(
        question=question, db=db, session_id="test",
    ):
        type_ = event.get("type", "?")
        counts[type_] = counts.get(type_, 0) + 1
        # Compact preview
        if type_ == "intent":
            print(f"  [intent] {event.get('intent')} complexity={event.get('complexity')} "
                  f"chart={event.get('wants_chart')} dashboard={event.get('wants_dashboard')}")
        elif type_ == "plan":
            print(f"  [plan] {event.get('title')!r} : "
                  f"{event.get('query_count')} queries, {event.get('visual_count')} visuals")
        elif type_ == "query_start":
            print(f"  [query_start] {event.get('query_id')}: {event.get('purpose')}")
        elif type_ == "query_done":
            ok = "OK" if event.get("success") else "FAIL"
            print(f"  [query_done]  {event.get('query_id')}: {ok}  "
                  f"{event.get('rows_count')} rows  {event.get('latency_ms')}ms")
        elif type_ == "viz":
            extras = []
            if event.get("kpi"):
                extras.append(f"kpi={event['kpi'].get('formatted_value')}")
            if event.get("echarts_option"):
                extras.append("echarts")
            if event.get("table_rows"):
                extras.append(f"table={len(event['table_rows'])} rows")
            extras_s = " | ".join(extras) if extras else (event.get("error") or "?")
            print(f"  [viz] {event.get('visual_id')} {event.get('type'):<14s} {event.get('title')!r:<50s}  {extras_s}")
        elif type_ == "dashboard_layout":
            print(f"  [dashboard_layout] {event.get('title')!r} "
                  f"with {len(event.get('layout', []))} rows, "
                  f"{len(event.get('visuals', []))} visuals")
        elif type_ == "insight":
            print(f"  [insight] {event.get('headline')!r}")
            for f in event.get("key_findings", [])[:3]:
                print(f"            * {f}")
        elif type_ == "critique":
            print(f"  [critique] passed={event.get('passed')} score={event.get('score')} "
                  f"issues={len(event.get('issues', []))}")
            for i in event.get("issues", []):
                print(f"            [{i.get('severity'):<7s}] {i.get('category'):<14s} {i.get('message')}")
        elif type_ == "usage":
            print(f"  [usage] intent={event.get('intent_latency_ms')}ms "
                  f"plan={event.get('plan_latency_ms')}ms "
                  f"insight={event.get('insight_latency_ms')}ms "
                  f"total={event.get('total_elapsed_ms')}ms")
        elif type_ == "done":
            print(f"  [done] elapsed_ms={event.get('elapsed_ms')}")
        elif type_ == "token":
            pass  # too noisy
        elif type_ in ("chart_spec", "table_data", "export_ctx"):
            pass  # backwards-compat events; suppress
        else:
            print(f"  [{type_}] {event}")

    total = (time.perf_counter() - t0) * 1000
    print()
    print("=" * 80)
    print(f"Event counts: {counts}")
    print(f"Wall-clock total: {total:,.0f} ms")


if __name__ == "__main__":
    asyncio.run(go())
