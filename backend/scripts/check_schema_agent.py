"""Smoke test the Schema Agent against the AUA/KUA database."""
import asyncio
import time

from langchain_community.utilities import SQLDatabase

from app.agents.schema_agent import get_schema_context

PG_URI = "postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo"


async def go() -> None:
    db = SQLDatabase.from_uri(PG_URI)

    print("First call (cold cache, doing full profile)...")
    t0 = time.perf_counter()
    ctx = await get_schema_context("test-session", db)
    t1 = (time.perf_counter() - t0) * 1000
    print(f"  Built in {t1:,.0f} ms\n")

    print(f"Dialect: {ctx.dialect}")
    print(f"Summary: {ctx.summary}\n")

    print("Per-table profiles:")
    for tname, tp in ctx.profiles.items():
        print(f"  {tname:<22s}  rows={tp.row_count:>10,}  cols={len(tp.columns)}")

    # Print a portion of the compact DDL
    print("\nCompact DDL (first 80 lines):")
    print("-" * 80)
    for line in ctx.ddl.splitlines()[:80]:
        print(line)
    print("-" * 80)

    # Second call should hit the cache
    print("\nSecond call (warm cache):")
    t0 = time.perf_counter()
    ctx2 = await get_schema_context("test-session", db)
    t2 = (time.perf_counter() - t0) * 1000
    print(f"  Returned in {t2:,.1f} ms (should be <50ms)")
    print(f"  Same content: {ctx.summary == ctx2.summary}")


if __name__ == "__main__":
    asyncio.run(go())
