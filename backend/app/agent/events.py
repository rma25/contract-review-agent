import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import ActorType
from app.repositories.audit_repository import AuditRepository

AGENT_ACTOR = "contract-review-agent"


def record_agent_event(
    session: Session, event_type: str, contract_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    """Append an agent audit event in the caller's transaction. Payloads hold metadata only."""
    AuditRepository(session).append(
        event_type=event_type,
        actor_type=ActorType.AGENT,
        actor=AGENT_ACTOR,
        contract_id=contract_id,
        payload=payload,
    )
