import json
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.llm import get_llm_provider
from app.llm.audited import AuditedLLMProvider
from app.llm.base import LLMError, LLMRateLimitError, LLMRequestError, LLMServerError
from app.llm.retry import RetryPolicy
from app.llm.types import ChatResponse, Message, TokenUsage, ToolCall
from app.models import ActorType, AuditEvent, Contract
from app.prompts import load_prompt
from tests.llm.mock_http import MockHTTP, json_reply

SECRET_PROMPT = "TOP-SECRET-CLAUSE-TEXT"
SECRET_KEY = "sk-SENTINEL-do-not-log"


class FakeProvider:
    name = "fake"

    def __init__(self, *outcomes: ChatResponse | LLMError, model: str = "fake-model") -> None:
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0

    def chat(self, messages, tools=None, response_schema=None) -> ChatResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, LLMError):
            raise outcome
        return outcome


def _ok(**overrides) -> ChatResponse:
    values = {
        "text": "fine",
        "usage": TokenUsage(input_tokens=10, output_tokens=4, total_tokens=14),
        "provider": "fake",
        "model": "fake-model-001",
        "finish_reason": "stop",
    } | overrides
    return ChatResponse(**values)


def _audited(
    inner: FakeProvider, session_factory: Callable[[], Session], max_attempts: int = 3
) -> AuditedLLMProvider:
    return AuditedLLMProvider(
        inner,
        session_factory=session_factory,
        retry_policy=RetryPolicy(max_attempts=max_attempts),
        sleep=lambda _: None,
    )


def _events(session: Session, model: str, event_type: str | None = None) -> list[AuditEvent]:
    """LLM audit events for one model name.

    Scoped by model because test_failure_event_survives_caller_rollback really commits.
    """
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.actor == "llm", AuditEvent.payload["model"].astext == model)
        .order_by(AuditEvent.id)
    )
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    return list(session.scalars(stmt))


def test_success_writes_audit_event(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    inner = FakeProvider(
        LLMRateLimitError("429"),
        _ok(tool_calls=[ToolCall(id="c1", name="lookup_rule")]),
    )
    contract = Contract(
        original_filename="a.pdf",
        content_type="application/pdf",
        storage_key="k.pdf",
        size_bytes=1,
        sha256="0" * 64,
    )
    db_session.add(contract)
    db_session.commit()
    prompt = load_prompt("smoke")

    response = _audited(inner, session_factory).chat(
        [Message.user(SECRET_PROMPT)], prompt=prompt, contract_id=contract.id
    )

    assert response.text == "fine"
    [event] = _events(db_session, "fake-model")
    assert event.event_type == "llm.call.succeeded"
    assert event.actor_type == ActorType.AGENT
    assert event.contract_id == contract.id
    payload = event.payload
    assert payload["success"] is True
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-model"
    assert payload["response_model"] == "fake-model-001"
    assert payload["attempts"] == 2
    assert payload["input_tokens"] == 10
    assert payload["output_tokens"] == 4
    assert payload["total_tokens"] == 14
    assert payload["tool_names"] == ["lookup_rule"]
    assert payload["prompt_name"] == "smoke"
    assert payload["prompt_version"] == prompt.version
    assert payload["prompt_sha256"] == prompt.sha256
    assert isinstance(payload["latency_ms"], int)
    assert SECRET_PROMPT not in json.dumps(payload)


def test_failure_writes_audit_event_and_reraises(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    inner = FakeProvider(*(LLMServerError("upstream down", status_code=503) for _ in range(3)))

    with pytest.raises(LLMServerError):
        _audited(inner, session_factory, max_attempts=3).chat([Message.user(SECRET_PROMPT)])

    assert inner.calls == 3
    [event] = _events(db_session, "fake-model")
    assert event.event_type == "llm.call.failed"
    assert event.payload["success"] is False
    assert event.payload["attempts"] == 3
    assert event.payload["error_type"] == "LLMServerError"
    assert event.payload["status_code"] == 503
    assert "upstream down" not in json.dumps(event.payload)  # raw messages are not logged


def test_non_retryable_failure_is_audited_once(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    inner = FakeProvider(LLMRequestError("bad", status_code=400))

    with pytest.raises(LLMRequestError):
        _audited(inner, session_factory).chat([Message.user("x")])

    [event] = _events(db_session, "fake-model", "llm.call.failed")
    assert event.payload["attempts"] == 1


def test_failure_event_survives_caller_rollback(engine: Engine) -> None:
    """The audit event is committed on its own, even if the caller's work is rolled back."""
    model = f"rollback-{uuid.uuid4()}"
    real_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    inner = FakeProvider(LLMRequestError("bad", status_code=400), model=model)

    with real_sessions() as caller:
        caller.add(
            Contract(
                original_filename="b.pdf",
                content_type="application/pdf",
                storage_key="b.pdf",
                size_bytes=1,
                sha256="1" * 64,
            )
        )
        caller.flush()
        with pytest.raises(LLMRequestError):
            _audited(inner, real_sessions).chat([Message.user("x")])
        caller.rollback()

    with real_sessions() as check:
        events = _events(check, model, "llm.call.failed")
        assert len(events) == 1
        assert check.scalars(select(Contract).where(Contract.storage_key == "b.pdf")).all() == []


def test_end_to_end_never_logs_key_or_prompt(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    """Real factory + Gemini adapter over mocked HTTP: the key and prompt stay out of the log."""
    settings = Settings(
        _env_file=None,
        llm_provider="gemini",
        gemini_api_key=SECRET_KEY,
        gemini_model="gemini-test",
        llm_max_retries=1,
        llm_retry_base_seconds=0.0,
    )
    http = MockHTTP(
        json_reply(
            200,
            {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
            },
        ),
        json_reply(400, {"error": {"code": 400, "message": f"bad key {SECRET_KEY}"}}),
    )
    llm = get_llm_provider(settings, session_factory=session_factory, http_client=http.client)

    llm.chat([Message.user(SECRET_PROMPT)])
    with pytest.raises(LLMRequestError) as info:
        llm.chat([Message.user(SECRET_PROMPT)])

    assert SECRET_KEY not in str(info.value)
    for request in http.requests:
        assert SECRET_KEY not in str(request.url)
        assert SECRET_KEY not in request.content.decode()
    events = _events(db_session, "gemini-test")
    assert [e.event_type for e in events] == ["llm.call.succeeded", "llm.call.failed"]
    logged = json.dumps([e.payload for e in events])
    assert SECRET_KEY not in logged
    assert SECRET_PROMPT not in logged
