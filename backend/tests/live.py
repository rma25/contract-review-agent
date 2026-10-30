"""Helpers for opt-in live tests against the provider configured in .env."""

import httpx2
import pytest

from app.core.config import Settings


def skip_if_unconfigured(settings: Settings) -> None:
    if settings.llm_provider == "gemini" and not settings.gemini_api_key.get_secret_value():
        pytest.skip("GEMINI_API_KEY is not set")
    if settings.llm_provider == "groq" and not settings.groq_api_key.get_secret_value():
        pytest.skip("GROQ_API_KEY is not set")
    if settings.llm_provider == "ollama":
        try:
            httpx2.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2.0)
        except httpx2.HTTPError:
            pytest.skip(f"Ollama is not reachable at {settings.ollama_base_url}")
