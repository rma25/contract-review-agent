import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.llm import get_llm_provider
from app.llm.audited import AuditedLLMProvider
from app.llm.base import LLMConfigurationError
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.groq import GroqProvider
from app.llm.providers.ollama import OllamaProvider


def _no_db() -> Session:
    raise AssertionError("factory tests must not touch the database")


def _settings(**overrides) -> Settings:
    # _env_file=None: never let a developer's real .env leak into unit tests.
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    ("overrides", "adapter", "model"),
    [
        (
            {"llm_provider": "gemini", "gemini_api_key": "k", "gemini_model": "g-1"},
            GeminiProvider,
            "g-1",
        ),
        ({"llm_provider": "groq", "groq_api_key": "k", "groq_model": "q-1"}, GroqProvider, "q-1"),
        ({"llm_provider": "ollama", "ollama_model": "o-1"}, OllamaProvider, "o-1"),
    ],
)
def test_factory_selects_adapter(overrides: dict, adapter: type, model: str) -> None:
    llm = get_llm_provider(_settings(**overrides), session_factory=_no_db)

    assert isinstance(llm, AuditedLLMProvider)
    assert isinstance(llm.inner, adapter)
    assert llm.model == model


def test_default_provider_is_gemini() -> None:
    assert _settings().llm_provider == "gemini"


@pytest.mark.parametrize(
    ("provider", "env_var"), [("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY")]
)
def test_missing_api_key_is_a_configuration_error(provider: str, env_var: str) -> None:
    with pytest.raises(LLMConfigurationError, match=env_var):
        get_llm_provider(_settings(llm_provider=provider), session_factory=_no_db)


def test_unknown_provider_is_rejected_by_settings() -> None:
    with pytest.raises(ValidationError):
        _settings(llm_provider="openai")


def test_retry_policy_comes_from_settings() -> None:
    settings = _settings(
        llm_provider="ollama",
        llm_max_retries=5,
        llm_retry_base_seconds=0.5,
        llm_retry_max_seconds=8.0,
    )

    llm = get_llm_provider(settings, session_factory=_no_db)

    assert llm.retry_policy.max_attempts == 6
    assert llm.retry_policy.base_delay == 0.5
    assert llm.retry_policy.max_delay == 8.0


def test_api_keys_are_masked_in_settings_repr() -> None:
    settings = _settings(gemini_api_key="super-secret", groq_api_key="also-secret")

    assert "super-secret" not in repr(settings)
    assert "also-secret" not in str(settings.model_dump())
