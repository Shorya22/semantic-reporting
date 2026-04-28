"""Smoke test the Intent Classifier against representative questions."""
import asyncio
import time
from app.agents.intent_classifier import classify_intent

QUESTIONS = [
    # Deterministic short-circuits
    "Hi",
    "Hello!",
    "Good morning",
    "What can you do?",
    # Simple QA
    "How many auth transactions happened today?",
    "Total number of operators",
    # Single metric
    "What is the auth success rate?",
    # Exploration with implied chart
    "Show me monthly transaction trends",
    "Top 10 AUAs by transaction count",
    "Average response time per state",
    # Dashboard intent
    "Give me a complete performance overview of all AUAs",
    "Build me a dashboard for KYC activity",
    # Comparison
    "Compare success rate of FINGER vs IRIS auth",
    # Report intent
    "Generate a PDF report of last month's KYC activity",
    "Send me an excel summary of error logs",
    # Geographic
    "Show transaction volume by state",
    # Pure metric with time window
    "How many failed transactions in the last 7 days?",
]


async def main() -> None:
    print("=" * 100)
    print(f"{'#':>2}  {'INTENT':<13s}{'CHART':<6s}{'DASH':<5s}{'EXP':<5s}{'CMPLX':<10s}{'CONF':<6s}{'LAT':<6s}  QUESTION")
    print("=" * 100)
    overall_t0 = time.perf_counter()
    for i, q in enumerate(QUESTIONS, start=1):
        intent = await classify_intent(q)
        chart = "Y" if intent.wants_chart else " "
        dash  = "Y" if intent.wants_dashboard else " "
        exp   = (intent.wants_export or "")[:3]
        cmplx = intent.complexity[:9]
        conf  = f"{intent.confidence:.2f}"
        lat   = f"{intent.latency_ms}ms"
        print(f"{i:>2}  {intent.intent:<13s}{chart:<6s}{dash:<5s}{exp:<5s}{cmplx:<10s}{conf:<6s}{lat:<6s}  {q!r}")
    overall = int((time.perf_counter() - overall_t0) * 1000)
    print("-" * 100)
    print(f"Total wall-clock for {len(QUESTIONS)} questions: {overall} ms "
          f"(avg {overall // len(QUESTIONS)} ms/question)")


if __name__ == "__main__":
    asyncio.run(main())
