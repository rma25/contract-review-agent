from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Application settings, read from the environment and the repo-root .env file."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://contract:contract@localhost:5432/contract_review"
    test_database_url: str = (
        "postgresql+psycopg://contract:contract@localhost:5432/contract_review_test"
    )
    upload_dir: Path = Path("./uploads")
    max_upload_mb: int = 25

    # --- LLM ---
    llm_provider: Literal["gemini", "groq", "ollama"] = "gemini"
    llm_temperature: float | None = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_retry_base_seconds: float = 1.0
    llm_retry_max_seconds: float = 30.0

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"

    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
