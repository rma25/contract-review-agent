from collections.abc import Callable

import httpx2
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.llm.audited import AuditedLLMProvider
from app.llm.base import LLMConfigurationError, LLMProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.groq import GroqProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.retry import RetryPolicy


def build_provider(settings: Settings, http_client: httpx2.Client | None = None) -> LLMProvider:
    """Build the raw adapter selected by LLM_PROVIDER. Prefer get_llm_provider()."""
    match settings.llm_provider:
        case "gemini":
            return GeminiProvider(
                api_key=_require_key(settings.gemini_api_key.get_secret_value(), "GEMINI_API_KEY"),
                model=settings.gemini_model,
                timeout_seconds=settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                http_client=http_client,
            )
        case "groq":
            return GroqProvider(
                api_key=_require_key(settings.groq_api_key.get_secret_value(), "GROQ_API_KEY"),
                model=settings.groq_model,
                base_url=settings.groq_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                http_client=http_client,
            )
        case "ollama":
            return OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout_seconds=settings.llm_timeout_seconds,
                temperature=settings.llm_temperature,
                http_client=http_client,
            )
    raise LLMConfigurationError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}")


def get_llm_provider(
    settings: Settings | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    http_client: httpx2.Client | None = None,
) -> AuditedLLMProvider:
    """The entry point for all LLM access: the configured adapter, with retries and auditing."""
    settings = settings or get_settings()
    return AuditedLLMProvider(
        build_provider(settings, http_client),
        session_factory=session_factory or get_sessionmaker(),
        retry_policy=RetryPolicy(
            max_attempts=settings.llm_max_retries + 1,
            base_delay=settings.llm_retry_base_seconds,
            max_delay=settings.llm_retry_max_seconds,
        ),
    )


def _require_key(value: str, env_var: str) -> str:
    if not value.strip():
        raise LLMConfigurationError(f"{env_var} is not set")
    return value
