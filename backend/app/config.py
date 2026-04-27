"""Application configuration loaded from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings

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

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

# Ensure data directory exists ASAP so SQLAlchemy can create the SQLite file.
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
