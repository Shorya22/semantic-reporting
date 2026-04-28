"""
Pipeline-wide token-usage accumulator.

The multi-agent pipeline runs several LLM calls (intent → planner → SQL
workers → viz designer → insight → critic). Each agent does its own
``llm.ainvoke()`` and originally threw away the response's
``usage_metadata`` — the orchestrator reported only latency, never tokens,
so the UI showed ``↑0 ↓0 tok`` for every dashboard run.

Threading an accumulator through every function signature would touch
six agents, so we use a ``contextvars.ContextVar`` instead. Python's
``asyncio`` automatically forwards ``ContextVar`` state into spawned
tasks, so concurrent SQL workers all see the same bucket without any
extra plumbing.

Lifecycle
---------
1. ``orchestrator.run_pipeline_stream`` calls :func:`start_bucket` once
   at the top of the request.
2. Every agent that issues an LLM call passes the response (or an
   already-extracted ``{input_tokens, output_tokens}`` dict) to
   :func:`record`.
3. After the pipeline finishes, the orchestrator calls :func:`totals`
   and emits the result in its final ``usage`` event.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass
class _UsageBucket:
    input_tokens:  int = 0
    output_tokens: int = 0

    def add(self, inp: int, out: int) -> None:
        if inp:
            self.input_tokens += int(inp)
        if out:
            self.output_tokens += int(out)


_BUCKET: contextvars.ContextVar["_UsageBucket | None"] = contextvars.ContextVar(
    "agent_usage_bucket", default=None,
)


def start_bucket() -> _UsageBucket:
    """Initialise a fresh accumulator for the current pipeline run."""
    bucket = _UsageBucket()
    _BUCKET.set(bucket)
    return bucket


def record(response: Any) -> None:
    """Add the token counts from a LangChain LLM response to the active bucket.

    Accepts either a LangChain response object (with ``.usage_metadata``)
    or a plain ``dict`` of usage stats. Silently no-ops when no bucket is
    active (e.g. ad-hoc usage outside the multi-agent pipeline).
    """
    bucket = _BUCKET.get()
    if bucket is None:
        return

    meta: dict[str, Any] | None = None
    if isinstance(response, dict):
        meta = response
    else:
        meta = getattr(response, "usage_metadata", None)

    if not meta:
        return

    bucket.add(
        int(meta.get("input_tokens",  0) or 0),
        int(meta.get("output_tokens", 0) or 0),
    )


def totals() -> tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` for the current bucket."""
    bucket = _BUCKET.get()
    if bucket is None:
        return (0, 0)
    return (bucket.input_tokens, bucket.output_tokens)
