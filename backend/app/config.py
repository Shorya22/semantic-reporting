"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Per-agent LLM config
# ---------------------------------------------------------------------------

# Canonical agent names. Every multi-agent component reads its model /
# provider / max_tokens / temperature from these slots so individual agents
# can be swapped in `.env` without code changes.
AGENT_NAMES = (
    "intent_classifier",   # cheap + fast: classify intent in < 500 ms
    "planner",             # decomposes question into AnalysisPlan
    "schema",              # cached schema introspection (rarely an LLM call)
    "sql_agent",           # writes & runs SQL (per-query worker)
    "viz_designer",        # picks chart types from data shape
    "insight_agent",       # writes the executive narrative
    "critic",              # quality gate
)


@dataclass(frozen=True)
class AgentLLMConfig:
    """Resolved LLM config for one agent."""
    model:       str
    provider:    str         # "groq" | "ollama"
    max_tokens:  int
    temperature: float

# Resolve .env relative to this file so Settings() works regardless of the
# process working directory (FastAPI server, MCP stdio server, tests, etc.).
_BACKEND_DIR = Path(__file__).parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"
_DATA_DIR = _BACKEND_DIR / "data"


class Settings(BaseSettings):
    """
    Application settings loaded from the .env file or environment variables.
    """

    # ---- LLM ---------------------------------------------------------------
    groq_api_key: str = ""
    llm_provider: str = "groq"
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "llama-3.3-70b-versatile"
    agent_max_tokens: int = 512
    synthesis_max_tokens: int = 2048
    agent_max_iterations: int = 2

    # ---- HTTP --------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000

    # ---- Filesystem --------------------------------------------------------
    upload_dir: str = str(_BACKEND_DIR / "uploads")
    data_dir: str = str(_DATA_DIR)

    # ---- Application database ---------------------------------------------
    # Stores connection registry, conversations, messages, analyses, user prefs.
    # Default is a local SQLite file that requires zero ops; switch to Postgres
    # in production by setting APP_DB_URL=postgresql+psycopg2://user:pass@host/db
    app_db_url: str = f"sqlite:///{(_DATA_DIR / 'app.db').as_posix()}"
    app_db_echo: bool = False

    # ---- Redis cache -------------------------------------------------------
    # cache_backend chooses how the cache layer behaves:
    #   "redis"     — real Redis at redis_url; falls back to in-memory if
    #                 unreachable. Use in production.
    #   "fakeredis" — pure-Python in-process Redis-protocol server. Use for
    #                 tests and zero-setup local development. No daemon
    #                 required, no Docker, no network.
    #   "memory"    — skip Redis entirely; only the in-process TTLCache.
    cache_backend: str = "redis"
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    cache_schema_ttl: int = 60 * 60 * 24  # 1 day
    cache_query_ttl: int = 60 * 5         # 5 minutes
    cache_ollama_ttl: int = 60            # 1 minute

    # ---- Crypto ------------------------------------------------------------
    # Used to encrypt sensitive connection params (e.g. Postgres password) at
    # rest in the app database. Auto-generated and persisted on first run if
    # not provided, but ALWAYS set this in production via env.
    app_secret_key: str = ""

    # ---- Per-agent LLM config (multi-agent system) -------------------------
    # Each agent role can override model / provider / max_tokens / temperature
    # independently via .env. When a field is left blank or zero, the value
    # falls back to the global defaults above (default_model / llm_provider /
    # agent_max_tokens / 0.0).
    #
    # Example .env:
    #   MODEL_INTENT_CLASSIFIER=llama-3.1-8b-instant
    #   PROVIDER_INTENT_CLASSIFIER=groq
    #   MAX_TOKENS_INTENT_CLASSIFIER=300
    #   TEMP_INTENT_CLASSIFIER=0
    #
    # All eight are reachable through ``settings.agent_config("intent_classifier")``.

    # Intent Classifier — cheap + fast (haiku-equivalent on Groq)
    model_intent_classifier:      str   = "llama-3.1-8b-instant"
    provider_intent_classifier:   str   = "groq"
    max_tokens_intent_classifier: int   = 400
    temp_intent_classifier:       float = 0.0

    # Planner — decomposes question, needs reasoning
    model_planner:      str   = "llama-3.3-70b-versatile"
    provider_planner:   str   = "groq"
    max_tokens_planner: int   = 2048
    temp_planner:       float = 0.0

    # Schema agent — usually no LLM call (deterministic), but a model is
    # available if/when it needs to summarise schemas for downstream agents.
    model_schema:      str   = "llama-3.1-8b-instant"
    provider_schema:   str   = "groq"
    max_tokens_schema: int   = 1024
    temp_schema:       float = 0.0

    # SQL Agent — writes & runs SQL (must be tool-capable)
    model_sql_agent:      str   = "llama-3.3-70b-versatile"
    provider_sql_agent:   str   = "groq"
    max_tokens_sql_agent: int   = 1024
    temp_sql_agent:       float = 0.0

    # Viz Designer — auto-picks chart type from data shape
    model_viz_designer:      str   = "llama-3.1-8b-instant"
    provider_viz_designer:   str   = "groq"
    max_tokens_viz_designer: int   = 800
    temp_viz_designer:       float = 0.0

    # Insight Agent — writes the executive narrative
    model_insight_agent:      str   = "llama-3.3-70b-versatile"
    provider_insight_agent:   str   = "groq"
    max_tokens_insight_agent: int   = 1500
    temp_insight_agent:       float = 0.3   # slight creativity for prose

    # Critic — small model for quality checks
    model_critic:      str   = "llama-3.1-8b-instant"
    provider_critic:   str   = "groq"
    max_tokens_critic: int   = 400
    temp_critic:       float = 0.0

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}

    # ----------------------------------------------------------------------
    # Helper — resolve the per-agent LLM config by name
    # ----------------------------------------------------------------------
    def agent_config(self, agent_name: str) -> AgentLLMConfig:
        """
        Return the resolved (model, provider, max_tokens, temperature) for
        ``agent_name``. Unknown names raise ``KeyError`` so typos surface
        immediately at boot.

        When any per-agent slot is empty/zero we fall through to the global
        defaults — so dropping an env var has the effect of "use the default".
        """
        if agent_name not in AGENT_NAMES:
            raise KeyError(
                f"Unknown agent '{agent_name}'. Known: {', '.join(AGENT_NAMES)}"
            )

        model      = getattr(self, f"model_{agent_name}",      "") or self.default_model
        provider   = getattr(self, f"provider_{agent_name}",   "") or self.llm_provider
        max_tokens = getattr(self, f"max_tokens_{agent_name}", 0)  or self.agent_max_tokens
        temperature = getattr(self, f"temp_{agent_name}",       0.0)
        return AgentLLMConfig(
            model=model,
            provider=provider,
            max_tokens=max_tokens,
            temperature=float(temperature),
        )


settings = Settings()

# Ensure data directory exists ASAP so SQLAlchemy can create the SQLite file.
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
