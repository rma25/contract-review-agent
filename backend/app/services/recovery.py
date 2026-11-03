import logging
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ActorType, ContractStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.contract_repository import ContractRepository

logger = logging.getLogger(__name__)

RECOVERY_ACTOR = "stuck-job-recovery"
RECOVERY_REASON = "recovery:stuck_timeout"
IN_PROGRESS = frozenset({ContractStatus.EXTRACTING, ContractStatus.ANALYZING})


def recover_stuck_contracts(session: Session, *, idle_timeout: timedelta) -> list[uuid.UUID]:
    """Fail analyses that have shown no activity for `idle_timeout`, and commit.

    An analysis whose process died (a restart, a crash, a Lambda timeout) would otherwise
    sit in `extracting` or `analyzing` forever, and the status machine only allows a retry
    from `failed`. Idle time is measured from the last audited activity, so a long but
    live analysis in another process is left alone. A plain function, so it can also run
    on a schedule.
    """
    contracts = ContractRepository(session)
    audit = AuditRepository(session)
    recovered: list[uuid.UUID] = []
    try:
        for contract, idle_seconds in contracts.lock_idle(IN_PROGRESS, idle_timeout):
            audit.append(
                event_type="contract.recovered",
                actor_type=ActorType.SYSTEM,
                actor=RECOVERY_ACTOR,
                contract_id=contract.id,
                payload={"from_status": contract.status.value, "idle_seconds": int(idle_seconds)},
            )
            contracts.transition(
                contract,
                ContractStatus.FAILED,
                actor_type=ActorType.SYSTEM,
                actor=RECOVERY_ACTOR,
                reason=RECOVERY_REASON,
            )
            recovered.append(contract.id)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    if recovered:
        logger.warning("Marked %d stuck contract(s) as failed", len(recovered))
    return recovered


def run_startup_recovery(
    session_factory: Callable[[], Session], *, idle_timeout: timedelta
) -> list[uuid.UUID]:
    """Startup hook. Never raises: a recovery failure must not stop the API from starting."""
    try:
        with session_factory() as session:
            return recover_stuck_contracts(session, idle_timeout=idle_timeout)
    except Exception:
        logger.exception("Stuck-job recovery failed")
        return []
