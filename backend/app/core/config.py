from functools import lru_cache
from pathlib import Path

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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
