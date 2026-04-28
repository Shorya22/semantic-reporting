"""Print the raw planner LLM output to see why JSON parsing fails."""
import asyncio
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.utilities import SQLDatabase
from app.agents.intent_classifier import classify_intent
from app.agents.llm_factory import llm_for
from app.agents.planner import _SYSTEM_PROMPT, _USER_TEMPLATE, _truncate_schema


async def go():
    db = SQLDatabase.from_uri("postgresql+pg8000://postgres:Admin123@localhost:5432/aua_kua_demo")
    schema = db.get_table_info()
    q = "Give me a complete performance overview of all AUAs"
    intent = await classify_intent(q)

    llm = llm_for("planner")
    user_msg = _USER_TEMPLATE.format(
        schema_ddl=_truncate_schema(schema),
        intent_json=intent.model_dump_json(indent=2),
        question=q,
    )
    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ])
    raw = str(response.content)
    print("=" * 100)
    print("RAW OUTPUT (first 3000 chars):")
    print("=" * 100)
    print(raw[:3000])
    print("=" * 100)
    print(f"Total length: {len(raw)} chars")
    print(f"Starts with '{{': {raw.lstrip().startswith('{')}")
    print(f"Ends with '}}': {raw.rstrip().endswith('}')}")


if __name__ == "__main__":
    asyncio.run(go())
