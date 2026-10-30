from typing import Protocol

from app.llm.types import ChatResponse, Message, ResponseSchema, ToolSpec


class LLMProvider(Protocol):
    """A chat model behind one provider. Adapters translate to and from the provider API."""

    name: str
    model: str

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ChatResponse: ...


class LLMError(Exception):
    """Base error for LLM calls. Messages must never contain API keys or prompt text."""

    retryable = False

    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.attempts = 1


class LLMRateLimitError(LLMError):
    """HTTP 429. Retried."""

    retryable = True


class LLMServerError(LLMError):
    """HTTP 5xx, timeouts, and connection failures. Retried."""

    retryable = True


class LLMRequestError(LLMError):
    """Other 4xx: bad request, auth, unknown model. Not retried."""


class LLMResponseError(LLMError):
    """The provider replied, but the response could not be parsed."""


class LLMConfigurationError(LLMError):
    """Missing or invalid settings (e.g. no API key for the selected provider)."""


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header given in seconds. HTTP-date values are ignored."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def error_for_status(provider: str, status_code: int, retry_after: float | None = None) -> LLMError:
    message = f"{provider} returned HTTP {status_code}"
    if status_code == 429:
        return LLMRateLimitError(message, status_code=status_code, retry_after=retry_after)
    if status_code >= 500:
        return LLMServerError(message, status_code=status_code, retry_after=retry_after)
    return LLMRequestError(message, status_code=status_code)
