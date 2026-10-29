import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import ActorType, AuditEvent, AuditEventImmutableError
from app.repositories.audit_repository import AuditRepository


@pytest.fixture
def audit_event(db_session: Session) -> AuditEvent:
    return AuditRepository(db_session).append(
        event_type="test.event", actor_type=ActorType.SYSTEM, actor="pytest", payload={"n": 1}
    )


def test_append_assigns_increasing_ids(db_session: Session, audit_event: AuditEvent) -> None:
    second = AuditRepository(db_session).append(
        event_type="test.event", actor_type=ActorType.SYSTEM, actor="pytest"
    )

    assert second.id > audit_event.id
    assert second.payload == {}
    assert audit_event.created_at is not None


def test_orm_update_is_blocked(db_session: Session, audit_event: AuditEvent) -> None:
    audit_event.actor = "tampered"

    with pytest.raises(AuditEventImmutableError):
        db_session.flush()


def test_orm_delete_is_blocked(db_session: Session, audit_event: AuditEvent) -> None:
    db_session.delete(audit_event)

    with pytest.raises(AuditEventImmutableError):
        db_session.flush()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET actor = 'tampered' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
    ],
)
def test_database_trigger_blocks_raw_sql(
    db_session: Session, audit_event: AuditEvent, statement: str
) -> None:
    """The trigger protects the table even when the ORM guard is bypassed."""
    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement), {"id": audit_event.id})
