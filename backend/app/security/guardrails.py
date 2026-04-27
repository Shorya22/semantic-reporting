"""
Input-stage guardrails for the data-analysis agent.

Purpose
-------
Block requests that aren't about analysing data in the connected database
**before** any LLM token is generated. This is the first layer of the
defence-in-depth model:

    1. **Input guardrail** (this module)        — fast Python check
    2. **Agent system prompt**                  — LLM-level topic filter
    3. **SQL guard** (``app.security.sql_guard``) — AST-level read-only enforcement
    4. **DB connection**                        — engine-level read-only mode
    5. **UI affordances**                       — visible "READ-ONLY" markers

The earlier the layer, the cheaper a rejection. The later the layer, the
harder it is to bypass. All five run on every query.

Categories blocked here
-----------------------
* **Prompt injection** — "ignore previous instructions", "you are now …",
  role-play takeovers, system-prompt leakage requests, jailbreaks
* **Destructive intent** — natural-language asks to write, modify, or
  destroy data even if no SQL has been generated yet
* **Off-topic generation** — creative writing, code help unrelated to
  data, general world knowledge, conversational small-talk

A rejection here returns a deterministic, user-facing refusal string —
no LLM call, no tokens spent, no DB hit.

Tuning policy
-------------
* Be aggressive on prompt injection (high impact, hard to reach via false
  positive in normal data conversation).
* Be aggressive on destructive intent (the user explicitly asked for it
  in natural language; refusing is correct even if false-positive).
* Be conservative on off-topic — only catch obvious patterns. Subtle
  cases are handled by the agent's system prompt, which has its own
  refusal template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# Decision shape
# ---------------------------------------------------------------------------

GuardCategory = str  # "ok" | "empty" | "prompt_injection" | "destructive_intent" | "off_topic"


@dataclass(frozen=True)
class GuardrailDecision:
    """Outcome of an input-stage guardrail check.

    Attributes
    ----------
    allowed:
        True when the question may proceed to the agent.
    category:
        Coarse classification used for telemetry / UI badges.
    reason:
        Short developer-facing explanation (logged, not shown to the user).
    user_message:
        Frontend-displayable refusal text. Empty when ``allowed`` is True.
    """
    allowed:      bool
    category:     GuardCategory
    reason:       str
    user_message: str


# ---------------------------------------------------------------------------
# Pattern library
# ---------------------------------------------------------------------------

# Prompt injection — attempts to override the system role, leak the
# system prompt, or impersonate the system. Aggressive on purpose.
_PROMPT_INJECTION: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"ignore\s+(?:all\s+|the\s+|any\s+|previous\s+|prior\s+|above\s+)+(?:instructions?|rules?|prompts?|context|messages?|guidelines?)"
    r"|disregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier)"
    r"|forget\s+(?:all\s+|everything\s+|the\s+)?(?:above|previous|prior|you[' ]?(?:ve|re)\s+been\s+told)"
    r"|reveal\s+(?:the\s+|your\s+)?(?:system|hidden|internal|secret|original)\s+(?:prompt|instructions?|rules?|guidelines?)"
    r"|(?:print|show|repeat|output|expose)\s+(?:the\s+|your\s+)?(?:system|hidden|internal|secret|original)\s+(?:prompt|instructions?|rules?)"
    r"|what\s+(?:is|are)\s+(?:the\s+|your\s+)?(?:system|hidden|internal|secret|original)\s+(?:prompt|instructions?|rules?|message)"
    r"|you(?:\s+are|'re)\s+(?:now|actually|really|just)\s+(?:a|an)\s+(?!read[- ]?only\s+(?:data\s+)?(?:analyst|assistant))(?!data\s+(?:analyst|assistant))"
    r"|(?:act|behave|respond)\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a|an)\s+(?!read[- ]?only\s+)(?!data\s+(?:analyst|assistant))(?!sql\s+(?:analyst|assistant))(?!database\s+(?:analyst|assistant))(?!analyst)"
    r"|roleplay\s+as|pretend\s+(?:to\s+be|you\s+are)|simulate\s+being"
    r"|jailbreak|developer\s+mode|dan\s+mode|do\s+anything\s+now"
    r"|(?:override|bypass)\s+(?:safety|guardrails?|restrictions?|filters?)"
    r")\b",
    re.IGNORECASE,
)

# Destructive intent — explicit asks to write, modify, or destroy data
# in natural language. Caught here so we never even start the agent for
# obvious destructive prompts.
_DESTRUCTIVE_INTENT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    # drop|delete|truncate|erase|wipe — allow up to 4 noun-words between
    # the verb and the target noun, so "drop the users table" matches.
    r"drop\s+(?:[\w'\-]+\s+){0,4}(?:table|tables|database|databases|schema|schemas|index|indexes|view|views|column|columns)"
    r"|delete\s+(?:[\w'\-]+\s+){0,4}(?:table|tables|data|records?|rows?|users?|customers?|orders?|entries|files?|database|schema)"
    r"|truncate\s+(?:[\w'\-]+\s+){0,3}(?:table|tables|data)?"
    r"|(?:erase|wipe|clear|destroy|remove)\s+(?:[\w'\-]+\s+){0,4}(?:data|table|tables|database|records?|rows?|entries|users?|customers?)"
    r"|insert\s+(?:a\s+|an\s+|new\s+|another\s+|the\s+|some\s+)(?:record|row|user|entry|item|data)"
    r"|update\s+(?:the\s+|all\s+|my\s+|every\s+)(?:table|record|row|column|user|customer|order)"
    r"|alter\s+(?:the\s+|my\s+)?(?:table|schema|column|database)"
    r"|create\s+(?:a\s+|an\s+|new\s+|another\s+|the\s+)?(?:table|database|user|schema|index|view|trigger)"
    r"|grant\s+(?:privileges?|access|permissions?|select|all)"
    r"|revoke\s+(?:privileges?|access|permissions?)"
    r"|(?:add|insert|put)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:row|record|entry|user|customer|order|column)\s+(?:to|into|in)"
    r"|(?:modify|change|edit)\s+(?:the\s+|all\s+|my\s+)?(?:table|database|schema|record|row)"
    r")\b",
    re.IGNORECASE,
)

# Hard off-topic generation — clearly not about data, never overridden
# even when the user also mentions "my data". Targets explicit creative
# writing, code generation, conversational small-talk, and trivia.
_OFF_TOPIC: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"write\s+(?:me\s+)?(?:a|an|some|the)\s+(?:poem|poems|story|stories|essay|essays|song|songs|article|blog|letter|email|tweet|joke|jokes|riddle|haiku|sonnet|novel|chapter|script|fairy\s*tale|lyrics|rap)"
    r"|compose\s+(?:a|an|me|the)\s+(?:poem|story|essay|song|melody|tune)"
    r"|(?:tell|say)\s+(?:me\s+)?a\s+(?:joke|story|riddle|secret)"
    r"|tell\s+me\s+about\s+yoursel(?:f|ves)"
    r"|(?:who|what)\s+(?:are|is|made|created|built)\s+you\b"
    r"|what(?:\s+is|\'s)\s+your\s+name"
    r"|how\s+(?:are|do)\s+you\s+(?:doing|feel|feeling)\b"
    r"|what(?:\s+is|\'s)\s+(?:the\s+)?(?:capital|weather|population|meaning|purpose)\s+of\b"
    r"|who\s+(?:is|was)\s+(?:the\s+)?(?:president|ceo|founder|king|queen|prime\s+minister)"
    r"|what\s+time\s+is\s+it\b|what\s+day\s+is\s+(?:it|today)"
    r"|write\s+(?:me\s+)?(?:a|an|some|the)?\s*(?:python|javascript|typescript|java|c\+\+|rust|go|golang|ruby|php|html|css|sql|bash|shell|powershell)\s+(?:code|function|script|program|class|component|snippet)"
    r"|(?:write|build|create|implement)\s+(?:me\s+)?(?:a|an)?\s*(?:fibonacci|prime|sorting|search|hello\s+world)\s+(?:function|script|code|program|in)"
    r"|write\s+(?:me\s+)?(?:a|an|some|the)\s+(?:python|javascript|typescript|java|c\+\+|rust|go|golang|ruby|php|html|css)\s+\w+"
    r"|how\s+(?:do\s+i|to)\s+(?:write|code|implement|build)\s+(?:a|an)\s+(?:website|webapp|app|api|game)"
    r"|(?:solve|calculate)\s+(?:this\s+)?(?:math|equation|integral|derivative)"
    r")\b",
    re.IGNORECASE,
)

# Soft off-topic — patterns where a data-context word in the same
# question can plausibly flip the meaning back to data. Used together
# with _DATA_CONTEXT below.
_SOFT_OFF_TOPIC: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"explain\s+(?:quantum|relativity|history|biology|chemistry|physics|philosophy|evolution|the\s+universe|consciousness)"
    r"|translate\s+(?:this|the|that)\s+(?:to|into)\s+[a-z]+"
    r")\b",
    re.IGNORECASE,
)

# Data-context override — only applied to _SOFT_OFF_TOPIC. Hard off-topic
# patterns (creative writing, code generation, conversational) are NEVER
# overridden, even if the user mentions "my data".
_DATA_CONTEXT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"data|dataset|database|table|tables|column|columns|row|rows|record|records"
    r"|query|queries|select|where|group\s+by|order\s+by|join|joins"
    r"|schema|sql|csv|excel|spreadsheet|sqlite|postgres(?:ql)?|mysql"
    r"|chart|graph|plot|visuali[sz]e|trend|trends|aggregate|aggregation"
    r"|count|sum|avg|average|mean|median|min|max|total|distinct"
    r"|expense|expenses|revenue|sale|sales|order|orders|customer|customers|product|products|user|users|invoice|invoices"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# User-facing refusal templates
# ---------------------------------------------------------------------------

_REFUSAL_PROMPT_INJECTION = (
    "I'm a **read-only data analysis assistant** for your connected database. "
    "I can't change my role, reveal internal instructions, or follow instructions that override my purpose.\n\n"
    "Try asking a question about your data instead — for example:\n"
    "• *Show me the top 5 categories by revenue*\n"
    "• *What's the trend over the last 6 months?*\n"
    "• *List the tables and their row counts*"
)

_REFUSAL_DESTRUCTIVE = (
    "This is a **read-only assistant** — I can analyse and visualise data, "
    "but I cannot modify, insert, update, delete, or write to the database in any way.\n\n"
    "Try a SELECT-style question instead, e.g.:\n"
    "• *Show me the 10 most recent orders*\n"
    "• *How many users were created last month?*\n"
    "• *List the columns in the `customers` table*"
)

_REFUSAL_OFF_TOPIC = (
    "I'm a **data analysis assistant** — I only work with the data in your connected database. "
    "I can't help with general writing, coding, math, or world-knowledge questions.\n\n"
    "Try asking something like:\n"
    "• *What are the top 5 categories by total sales?*\n"
    "• *Show me the monthly trend with a chart*\n"
    "• *Which tables are available?*"
)

_REFUSAL_EMPTY = "Please ask a question about your data."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_question(question: str) -> GuardrailDecision:
    """Run all input-stage guardrails on a user's question.

    Order matters:
      1. Empty / too-short input
      2. Prompt injection  (highest severity)
      3. Destructive intent
      4. Off-topic generation (with data-context override)

    The first matching category wins; remaining checks are skipped.
    """
    if not question or not question.strip():
        return GuardrailDecision(False, "empty", "empty input", _REFUSAL_EMPTY)

    text = question.strip()

    # 1. Prompt injection — never override.
    if _PROMPT_INJECTION.search(text):
        return GuardrailDecision(
            False, "prompt_injection",
            "prompt-injection pattern detected",
            _REFUSAL_PROMPT_INJECTION,
        )

    # 2. Destructive intent — never override.
    if _DESTRUCTIVE_INTENT.search(text):
        return GuardrailDecision(
            False, "destructive_intent",
            "destructive intent (write/modify/delete) detected",
            _REFUSAL_DESTRUCTIVE,
        )

    # 3a. Hard off-topic — creative writing, code in another language,
    #     conversational small-talk. Never overridden by data context;
    #     "write me a poem about my data" is still off-topic.
    if _OFF_TOPIC.search(text):
        return GuardrailDecision(
            False, "off_topic",
            "hard off-topic generation request",
            _REFUSAL_OFF_TOPIC,
        )

    # 3b. Soft off-topic — patterns where a data-context word in the same
    #     question can plausibly flip the meaning back to data analysis
    #     (e.g. "translate this column to upper case" mentions a column).
    if _SOFT_OFF_TOPIC.search(text) and not _DATA_CONTEXT.search(text):
        return GuardrailDecision(
            False, "off_topic",
            "soft off-topic request without data context",
            _REFUSAL_OFF_TOPIC,
        )

    return GuardrailDecision(True, "ok", "passed", "")
