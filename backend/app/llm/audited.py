import random
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.llm.base import LLMError, LLMProvider
from app.llm.retry import RetryPolicy, call_with_retry
from app.llm.types import ChatResponse, Message, ResponseSchema, ToolSpec
from app.models import ActorType
from app.prompts import Prompt
from app.repositories.audit_repository import AuditRepository

AGENT_ACTOR = "llm"


class AuditedLLMProvider:
    """Wraps a provider adapter with retries and an audit event for every call.

    Audit payloads hold metadata only (provider, model, latency, tokens, outcome). Message
    content, prompts, responses, API keys, and raw error messages are never recorded.

    Each event is written and committed in its own session, so a failed call is still on
    record if the caller's transaction rolls back. Commit a contract before making LLM calls
    that reference it, or the audit insert cannot see the contract row.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        session_factory: Callable[[], Session],
        retry_policy: RetryPolicy,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.inner = inner
        self.session_factory = session_factory
        self.retry_policy = retry_policy
        self._sleep = sleep
        self._rng = rng

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
        *,
        prompt: Prompt | None = None,
        contract_id: uuid.UUID | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "provider": self.name,
            "model": self.model,
            "message_count": len(messages),
            "tools_offered": len(tools or []),
            "structured_output": response_schema is not None,
        }
        if prompt is not None:
            payload |= {
                "prompt_name": prompt.name,
                "prompt_version": prompt.version,
                "prompt_sha256": prompt.sha256,
            }

        started = time.perf_counter()
        try:
            result = call_with_retry(
                lambda: self.inner.chat(messages, tools, response_schema),
                self.retry_policy,
                sleep=self._sleep,
                rng=self._rng,
            )
        except LLMError as error:
            payload |= {
                "success": False,
                "latency_ms": _elapsed_ms(started),
                "attempts": error.attempts,
                "error_type": type(error).__name__,
                "status_code": error.status_code,
            }
            self._record("llm.call.failed", payload, contract_id)
            raise

        response = result.value
        payload |= {
            "success": True,
            "latency_ms": _elapsed_ms(started),
            "attempts": result.attempts,
            "response_model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
            "finish_reason": response.finish_reason,
            "tool_call_count": len(response.tool_calls),
            "tool_names": [call.name for call in response.tool_calls],
        }
        self._record("llm.call.succeeded", payload, contract_id)
        return response

    def _record(
        self, event_type: str, payload: dict[str, Any], contract_id: uuid.UUID | None
    ) -> None:
        with self.session_factory() as session:
            AuditRepository(session).append(
                event_type=event_type,
                actor_type=ActorType.AGENT,
                actor=AGENT_ACTOR,
                contract_id=contract_id,
                payload=payload,
            )
            session.commit()


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
