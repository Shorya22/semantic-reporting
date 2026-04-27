"""Application configuration loaded from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings

# Resolve .env relative to this file so Settings() works regardless of the
# process working directory (FastAPI server, MCP stdio server, tests, etc.).
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    """
    Application settings loaded from the .env file or environment variables.

    Attributes:
        groq_api_key: API key for Groq Cloud LLM inference (required only when llm_provider="groq").
        llm_provider: Which LLM backend to use — "groq" (GroqCloud) or "ollama" (local).
        ollama_base_url: Base URL of the local Ollama server (used when llm_provider="ollama").
        host: Host address the FastAPI server binds to.
        port: Port the FastAPI server listens on.
        upload_dir: Directory where uploaded CSV/Excel files are temporarily stored.
        default_model: Model ID used when the caller does not specify one.
    """

    groq_api_key: str = ""
    llm_provider: str = "groq"
    ollama_base_url: str = "http://localhost:11434"
    host: str = "0.0.0.0"
    port: int = 8000
    upload_dir: str = "uploads"
    default_model: str = "llama-3.3-70b-versatile"
    agent_max_tokens: int = 512      # decision LLM — only outputs <sql>, 512 is enough
    synthesis_max_tokens: int = 2048  # synthesis LLM — user-facing answer
    agent_max_iterations: int = 2    # max agent→execute loops; 1 = fastest (1 query then synthesize)

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
