from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.models import ActorType, AuditEvent, Contract, ContractStatus
from app.services.analysis_service import AnalysisService
from app.services.recovery import recover_stuck_contracts, run_startup_recovery

TIMEOUT = timedelta(minutes=30)

MakeStuck = Callable[..., Contract]


@pytest.fixture
def make_contract_at(db_session: Session) -> MakeStuck:
    """Insert a contract whose last activity was `idle` ago (on the database clock)."""

    def make(status: ContractStatus, idle: timedelta, *, last_event: timedelta | None = None):
        contract = Contract(
            original_filename="c.docx",
            content_type="application/octet-stream",
            storage_key="c.docx",
            size_bytes=1,
            sha256="0" * 64,
            status=status,
        )
        db_session.add(contract)
        db_session.flush()
        db_session.execute(
            update(Contract)
            .where(Contract.id == contract.id)
            .values(updated_at=text("now() - make_interval(secs => :secs)"))
            .execution_options(synchronize_session=False),
            {"secs": idle.total_seconds()},
        )
        if last_event is not None:
            # An insert with an explicit timestamp: audit rows are never updated.
            db_session.execute(
                text(
                    "INSERT INTO audit_events (contract_id, event_type, actor_type, actor, "
                    "payload, created_at) VALUES (:id, 'agent.tool_call', 'agent', 'test', "
                    "'{}', now() - make_interval(secs => :secs))"
                ),
                {"id": contract.id, "secs": last_event.total_seconds()},
            )
        db_session.commit()
        return contract

    return make


def _events(session: Session, contract: Contract) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.contract_id == contract.id).order_by(AuditEvent.id)
    return list(session.scalars(stmt))


def _status(session: Session, contract: Contract) -> ContractStatus:
    session.expire_all()
    refreshed = session.get(Contract, contract.id)
    assert refreshed is not None
    return refreshed.status


@pytest.mark.parametrize("status", [ContractStatus.EXTRACTING, ContractStatus.ANALYZING])
def test_idle_in_progress_contracts_are_failed_with_an_audited_reason(
    db_session: Session, make_contract_at: MakeStuck, status: ContractStatus
) -> None:
    contract = make_contract_at(status, timedelta(hours=2), last_event=timedelta(hours=1))

    recovered = recover_stuck_contracts(db_session, idle_timeout=TIMEOUT)

    assert recovered == [contract.id]
    assert _status(db_session, contract) == ContractStatus.FAILED
    recovered_event, status_event = _events(db_session, contract)[-2:]
    assert recovered_event.event_type == "contract.recovered"
    assert recovered_event.actor_type == ActorType.SYSTEM
    assert recovered_event.actor == "stuck-job-recovery"
    assert recovered_event.payload["from_status"] == status.value
    assert 3590 <= recovered_event.payload["idle_seconds"] <= 3610
    assert status_event.event_type == "contract.status_changed"
    assert status_event.actor_type == ActorType.SYSTEM
    assert status_event.payload == {
        "from": status.value,
        "to": "failed",
        "reason": "recovery:stuck_timeout",
    }


def test_recent_audit_activity_keeps_a_long_analysis_alive(
    db_session: Session, make_contract_at: MakeStuck
) -> None:
    # Started hours ago, but the agent made a tool call five minutes ago.
    contract = make_contract_at(
        ContractStatus.ANALYZING, timedelta(hours=3), last_event=timedelta(minutes=5)
    )

    assert recover_stuck_contracts(db_session, idle_timeout=TIMEOUT) == []
    assert _status(db_session, contract) == ContractStatus.ANALYZING


def test_recently_updated_contract_without_events_is_left_alone(
    db_session: Session, make_contract_at: MakeStuck
) -> None:
    contract = make_contract_at(ContractStatus.EXTRACTING, timedelta(minutes=10))

    assert recover_stuck_contracts(db_session, idle_timeout=TIMEOUT) == []
    assert _status(db_session, contract) == ContractStatus.EXTRACTING


@pytest.mark.parametrize(
    "status",
    [
        ContractStatus.UPLOADED,
        ContractStatus.AWAITING_REVIEW,
        ContractStatus.COMPLETED,
        ContractStatus.FAILED,
    ],
)
def test_contracts_not_in_progress_are_never_touched(
    db_session: Session, make_contract_at: MakeStuck, status: ContractStatus
) -> None:
    contract = make_contract_at(status, timedelta(days=2))

    assert recover_stuck_contracts(db_session, idle_timeout=TIMEOUT) == []
    assert _status(db_session, contract) == status
    assert _events(db_session, contract) == []


def test_recovery_is_idempotent(db_session: Session, make_contract_at: MakeStuck) -> None:
    contract = make_contract_at(ContractStatus.ANALYZING, timedelta(hours=2))

    assert recover_stuck_contracts(db_session, idle_timeout=TIMEOUT) == [contract.id]
    assert recover_stuck_contracts(db_session, idle_timeout=TIMEOUT) == []
    types = [e.event_type for e in _events(db_session, contract)]
    assert types.count("contract.recovered") == 1


def test_recovered_contract_can_be_analyzed_again(
    db_session: Session, make_contract_at: MakeStuck
) -> None:
    contract = make_contract_at(ContractStatus.ANALYZING, timedelta(hours=2))
    recover_stuck_contracts(db_session, idle_timeout=TIMEOUT)

    # The status machine allows failed -> extracting, so a human can retry.
    restarted = AnalysisService(db_session).start(contract.id)

    assert restarted.status == ContractStatus.EXTRACTING


def test_app_startup_runs_recovery(
    db_session: Session,
    session_factory: Callable[[], Session],
    make_contract_at: MakeStuck,
) -> None:
    stuck = make_contract_at(ContractStatus.ANALYZING, timedelta(hours=2))

    app = create_app(
        session_factory=session_factory,
        settings=Settings(_env_file=None, agent_stuck_timeout_minutes=30),
    )
    with TestClient(app):
        pass

    assert _status(db_session, stuck) == ContractStatus.FAILED


def test_startup_recovery_never_raises() -> None:
    def broken_factory() -> Session:
        raise RuntimeError("database is down")

    assert run_startup_recovery(broken_factory, idle_timeout=TIMEOUT) == []
