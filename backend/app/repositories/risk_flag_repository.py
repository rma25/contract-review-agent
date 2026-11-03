import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Clause, RiskFlag


class RiskFlagRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, flag: RiskFlag) -> RiskFlag:
        self.session.add(flag)
        self.session.flush()
        return flag

    def exists(self, clause_id: uuid.UUID, rule_id: str) -> bool:
        stmt = select(RiskFlag.id).where(
            RiskFlag.clause_id == clause_id, RiskFlag.rule_id == rule_id
        )
        return self.session.scalars(stmt).first() is not None

    def list_for_contract(self, contract_id: uuid.UUID) -> list[RiskFlag]:
        stmt = (
            select(RiskFlag)
            .join(RiskFlag.clause)
            .where(Clause.contract_id == contract_id)
            .options(joinedload(RiskFlag.clause))
            .order_by(Clause.ordinal, RiskFlag.score.desc(), RiskFlag.rule_id)
        )
        return list(self.session.scalars(stmt))
