"""Opt-in smoke test against the real provider in .env. Run with: uv run pytest -m live"""

from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.llm import Message, get_llm_provider
from app.models import AuditEvent
from app.prompts import load_prompt
from tests.live import skip_if_unconfigured

pytestmark = pytest.mark.live


def test_live_provider_round_trip(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    settings = Settings()
    skip_if_unconfigured(settings)
    prompt = load_prompt("smoke")
    llm = get_llm_provider(settings, session_factory=session_factory)

    response = llm.chat(
        [Message.system(prompt.render(word="pong")), Message.user("Go.")], prompt=prompt
    )

    assert "pong" in response.text.lower()
    assert response.usage.input_tokens > 0
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "llm.call.succeeded")
    ).one()
    assert event.payload["provider"] == settings.llm_provider
    assert event.payload["prompt_name"] == "smoke"
