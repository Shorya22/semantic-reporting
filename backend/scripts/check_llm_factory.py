"""Quick smoke test for the per-agent LLM factory."""
import json
from app.agents.llm_factory import describe_agent_models, llm_for

print("=" * 70)
print("Per-agent LLM config (resolved):")
print("=" * 70)
print(json.dumps(describe_agent_models(), indent=2))

print("\n" + "=" * 70)
print("Instantiation tests:")
print("=" * 70)

m = llm_for("intent_classifier")
mn = getattr(m, "model_name", None) or getattr(m, "model", None)
print(f"  intent_classifier  -> {type(m).__name__}  model={mn}")

m = llm_for("sql_agent", streaming=True)
mn = getattr(m, "model_name", None) or getattr(m, "model", None)
print(f"  sql_agent          -> {type(m).__name__}  model={mn}  streaming={getattr(m, 'streaming', None)}")

m = llm_for("planner", override_model="qwen/qwen3-32b")
mn = getattr(m, "model_name", None) or getattr(m, "model", None)
print(f"  planner (override) -> {type(m).__name__}  model={mn}")

m = llm_for("insight_agent")
mn = getattr(m, "model_name", None) or getattr(m, "model", None)
print(f"  insight_agent      -> {type(m).__name__}  model={mn}  temp={getattr(m, 'temperature', None)}")

print("\nAll tests passed.")
