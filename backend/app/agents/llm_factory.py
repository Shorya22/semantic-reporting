"""
Per-agent LLM factory — single source of truth for constructing chat models.

Every agent in the multi-agent system calls ``llm_for(agent_name)`` (or
``llm_for(agent_name, streaming=True)`` for streaming token output) and
receives a fully-configured ``BaseChatModel`` instance.

Why this exists
---------------
* Each agent role is configurable from ``.env`` independently (model,
  provider, max_tokens, temperature) — see ``app.config.AGENT_NAMES``.
* Per-request overrides from the API (``QueryRequest.model`` /
  ``QueryRequest.provider``) take priority over the per-agent defaults so a
  user can A/B-test the whole pipeline by switching one model.
* Centralises the Groq / Ollama branching — the rest of the codebase
  never imports ``ChatGroq`` or ``ChatOllama`` directly.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_community.chat_models import ChatOllama
from langchain_groq import ChatGroq

from app.config import AGENT_NAMES, settings


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def llm_for(
    agent_name: str,
    *,
    streaming: bool = False,
    override_model:    Optional[str] = None,
    override_provider: Optional[str] = None,
    override_max_tokens: Optional[int] = None,
) -> Any:
    """
    Build the LLM instance for the given agent role.

    Resolution order (highest precedence first):

    1. ``override_*`` arguments — per-call override. Used by the orchestrator
       when the user supplies ``QueryRequest.model`` / ``provider``.
    2. ``settings.agent_config(agent_name)`` — per-agent ``.env`` config.
    3. Global defaults baked into ``Settings``.

    Parameters
    ----------
    agent_name :
        One of ``app.config.AGENT_NAMES``. Unknown names raise ``KeyError``.
    streaming :
        Whether the model should emit token-by-token streaming events.
        Required for SSE token streaming from the agent_node.
    override_model / override_provider / override_max_tokens :
        Per-call overrides. ``None`` means "use the agent's default".
    """
    if agent_name not in AGENT_NAMES:
        raise KeyError(
            f"Unknown agent '{agent_name}'. Known agents: {', '.join(AGENT_NAMES)}"
        )

    cfg = settings.agent_config(agent_name)

    model      = override_model      or cfg.model
    provider   = (override_provider  or cfg.provider).lower()
    max_tokens = override_max_tokens or cfg.max_tokens

    if provider == "ollama":
        # ChatOllama doesn't accept max_tokens directly; the corresponding
        # knob is `num_predict`. temperature is honoured.
        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=cfg.temperature,
            num_predict=max_tokens,
        )

    # Default to Groq Cloud
    return ChatGroq(
        model=model,
        api_key=settings.groq_api_key,
        temperature=cfg.temperature,
        max_tokens=max_tokens,
        streaming=streaming,
    )


# ---------------------------------------------------------------------------
# Diagnostics — handy in /health and dev REPLs
# ---------------------------------------------------------------------------

def describe_agent_models() -> dict[str, dict[str, Any]]:
    """
    Return a {agent_name: {model, provider, max_tokens, temperature}} map.
    Used by /health and /config for visibility into the active agent setup.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in AGENT_NAMES:
        cfg = settings.agent_config(name)
        out[name] = {
            "model":       cfg.model,
            "provider":    cfg.provider,
            "max_tokens":  cfg.max_tokens,
            "temperature": cfg.temperature,
        }
    return out
