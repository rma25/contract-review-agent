import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ActorType, Contract, ContractStatus
from app.repositories.audit_repository import AuditRepository

# The only legal status changes. Re-analysis is allowed from failed only.
ALLOWED_TRANSITIONS: dict[ContractStatus, frozenset[ContractStatus]] = {
    ContractStatus.UPLOADED: frozenset({ContractStatus.EXTRACTING}),
    ContractStatus.FAILED: frozenset({ContractStatus.EXTRACTING}),
    ContractStatus.EXTRACTING: frozenset({ContractStatus.ANALYZING, ContractStatus.FAILED}),
    ContractStatus.ANALYZING: frozenset({ContractStatus.AWAITING_REVIEW, ContractStatus.FAILED}),
    ContractStatus.AWAITING_REVIEW: frozenset(),
    ContractStatus.COMPLETED: frozenset(),
}


class InvalidStatusTransitionError(ValueError):
    def __init__(self, current: ContractStatus, target: ContractStatus) -> None:
        super().__init__(f"Cannot move a contract from {current.value} to {target.value}")
        self.current = current
        self.target = target


class ContractRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, contract: Contract) -> Contract:
        self.session.add(contract)
        self.session.flush()
        return contract

    def get(self, contract_id: uuid.UUID) -> Contract | None:
        stmt = (
            select(Contract)
            .where(Contract.id == contract_id)
            .options(selectinload(Contract.clauses))
        )
        return self.session.scalars(stmt).one_or_none()

    def get_for_update(self, contract_id: uuid.UUID) -> Contract | None:
        """Load and row-lock a contract so concurrent status changes serialize."""
        stmt = (
            select(Contract)
            .where(Contract.id == contract_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.scalars(stmt).one_or_none()

    def transition(
        self,
        contract: Contract,
        target: ContractStatus,
        *,
        actor_type: ActorType,
        actor: str,
        reason: str | None = None,
    ) -> None:
        """Change status and audit it in the caller's transaction. Raises on illegal moves."""
        current = contract.status
        if target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidStatusTransitionError(current, target)
        contract.status = target
        payload: dict[str, Any] = {"from": current.value, "to": target.value}
        if reason is not None:
            payload["reason"] = reason
        AuditRepository(self.session).append(
            event_type="contract.status_changed",
            actor_type=actor_type,
            actor=actor,
            contract_id=contract.id,
            payload=payload,
        )
