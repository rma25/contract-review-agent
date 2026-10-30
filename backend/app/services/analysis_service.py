import uuid

from sqlalchemy.orm import Session

from app.models import ActorType, Contract, ContractStatus, RiskFlag
from app.repositories.contract_repository import ContractRepository
from app.repositories.risk_flag_repository import RiskFlagRepository
from app.services.contract_service import ANONYMOUS_ACTOR


class ContractNotFoundError(LookupError):
    pass


class AnalysisService:
    """Starts analyses and reads their results. The analysis itself runs in app.agent."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.contracts = ContractRepository(session)
        self.flags = RiskFlagRepository(session)

    def start(self, contract_id: uuid.UUID) -> Contract:
        """Move the contract to `extracting` so a background run can pick it up.

        The row lock makes a second concurrent request fail with
        InvalidStatusTransitionError instead of starting a duplicate run.
        """
        contract = self.contracts.get_for_update(contract_id)
        if contract is None:
            raise ContractNotFoundError(contract_id)
        try:
            self.contracts.transition(
                contract,
                ContractStatus.EXTRACTING,
                actor_type=ActorType.HUMAN,
                actor=ANONYMOUS_ACTOR,
                reason="analysis_requested",
            )
            self.session.commit()
        except BaseException:
            self.session.rollback()
            raise
        refreshed = self.contracts.get(contract_id)
        assert refreshed is not None
        return refreshed

    def list_flags(self, contract_id: uuid.UUID) -> list[RiskFlag]:
        if self.session.get(Contract, contract_id) is None:
            raise ContractNotFoundError(contract_id)
        return self.flags.list_for_contract(contract_id)
