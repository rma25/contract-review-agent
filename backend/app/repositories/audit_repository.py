import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import ActorType, AuditEvent


class AuditRepository:
    """Append-only access to audit_events. There is deliberately no update or delete."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(
        self,
        *,
        event_type: str,
        actor_type: ActorType,
        actor: str,
        contract_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            actor_type=actor_type,
            actor=actor,
            contract_id=contract_id,
            payload=payload or {},
        )
        self.session.add(event)
        self.session.flush()
        return event
