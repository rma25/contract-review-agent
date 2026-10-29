import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Contract


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
