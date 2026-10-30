import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActorType, AuditEvent, Contract, ContractStatus
from app.repositories.contract_repository import (
    ALLOWED_TRANSITIONS,
    ContractRepository,
    InvalidStatusTransitionError,
)

S = ContractStatus


def _contract(session: Session, status: ContractStatus) -> Contract:
    contract = Contract(
        original_filename="c.pdf",
        content_type="application/pdf",
        storage_key="c.pdf",
        size_bytes=1,
        sha256="0" * 64,
        status=status,
    )
    session.add(contract)
    session.flush()
    return contract


def _events(session: Session, contract: Contract) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.contract_id == contract.id)))


@pytest.mark.parametrize(
    ("current", "target"),
    [(current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets],
)
def test_allowed_transitions_are_audited(
    db_session: Session, current: ContractStatus, target: ContractStatus
) -> None:
    contract = _contract(db_session, current)

    ContractRepository(db_session).transition(
        contract, target, actor_type=ActorType.AGENT, actor="agent", reason="test"
    )
    db_session.commit()

    assert contract.status == target
    [event] = _events(db_session, contract)
    assert event.event_type == "contract.status_changed"
    assert event.actor_type == ActorType.AGENT
    assert event.payload == {"from": current.value, "to": target.value, "reason": "test"}


def test_the_happy_path_is_allowed() -> None:
    path = [S.UPLOADED, S.EXTRACTING, S.ANALYZING, S.AWAITING_REVIEW]
    for current, target in zip(path, path[1:], strict=False):
        assert target in ALLOWED_TRANSITIONS[current]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.UPLOADED, S.AWAITING_REVIEW),
        (S.UPLOADED, S.ANALYZING),
        (S.EXTRACTING, S.EXTRACTING),
        (S.ANALYZING, S.COMPLETED),
        (S.AWAITING_REVIEW, S.COMPLETED),  # only a human review flow may complete a contract
        (S.AWAITING_REVIEW, S.EXTRACTING),
        (S.COMPLETED, S.FAILED),
    ],
)
def test_illegal_transitions_raise_without_auditing(
    db_session: Session, current: ContractStatus, target: ContractStatus
) -> None:
    contract = _contract(db_session, current)

    with pytest.raises(InvalidStatusTransitionError):
        ContractRepository(db_session).transition(
            contract, target, actor_type=ActorType.AGENT, actor="agent"
        )

    assert contract.status == current
    assert _events(db_session, contract) == []
