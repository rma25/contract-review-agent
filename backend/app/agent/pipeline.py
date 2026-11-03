import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.budget import AgentBudget, AgentBudgetExceededError, AgentError
from app.agent.events import AGENT_ACTOR, record_agent_event
from app.agent.loop import RiskReviewAgent
from app.agent.segmentation import segment_contract
from app.core.config import Settings
from app.llm.audited import AuditedLLMProvider
from app.models import ActorType, Contract, ContractStatus
from app.playbook import load_playbook
from app.repositories.contract_repository import ContractRepository
from app.services.storage import LocalFileStorage
from app.services.text_extraction import extract_text

logger = logging.getLogger(__name__)


class ContractTooLongError(AgentError):
    pass


@dataclass(frozen=True)
class AnalysisRunner:
    """Everything a background analysis needs. A plain callable, so it can run in Lambda."""

    session_factory: Callable[[], Session]
    llm: AuditedLLMProvider
    storage: LocalFileStorage
    settings: Settings

    def __call__(self, contract_id: uuid.UUID) -> None:
        run_analysis(
            contract_id,
            session_factory=self.session_factory,
            llm=self.llm,
            storage=self.storage,
            settings=self.settings,
        )


@dataclass
class _Progress:
    stage: str = "extracting"


def run_analysis(
    contract_id: uuid.UUID,
    *,
    session_factory: Callable[[], Session],
    llm: AuditedLLMProvider,
    storage: LocalFileStorage,
    settings: Settings,
) -> None:
    """Analyze a contract that is in `extracting`, ending in `awaiting_review` or `failed`.

    Each stage commits its own work. Never raises: failures are recorded on the contract
    and in the audit log. The agent never moves a contract past `awaiting_review`.
    """
    progress = _Progress()
    with session_factory() as session:
        try:
            _analyze(session, contract_id, llm, storage, settings, progress)
        except Exception as exc:
            session.rollback()
            logger.exception("Analysis of contract %s failed at %s", contract_id, progress.stage)
            _mark_failed(session, contract_id, progress.stage, exc)


def _analyze(
    session: Session,
    contract_id: uuid.UUID,
    llm: AuditedLLMProvider,
    storage: LocalFileStorage,
    settings: Settings,
    progress: _Progress,
) -> None:
    contracts = ContractRepository(session)
    contract = contracts.get_for_update(contract_id)
    if contract is None or contract.status != ContractStatus.EXTRACTING:
        logger.warning("Contract %s is not waiting for analysis; skipping", contract_id)
        return

    text = extract_text(storage.path_for(contract.storage_key), contract.content_type)
    if len(text) > settings.agent_max_contract_chars:
        raise ContractTooLongError(
            f"Contract text is {len(text)} characters; the limit is "
            f"{settings.agent_max_contract_chars}"
        )
    contract.raw_text = text
    record_agent_event(session, "contract.text_extracted", contract_id, {"chars": len(text)})
    _transition(contracts, contract, ContractStatus.ANALYZING)
    session.commit()

    playbook = load_playbook(settings.playbook_path)
    budget = AgentBudget(
        max_iterations=settings.agent_max_iterations, token_budget=settings.agent_token_budget
    )

    progress.stage = "segmenting"
    clauses = segment_contract(
        session, llm, playbook, contract_id=contract_id, raw_text=text, budget=budget
    )

    progress.stage = "reviewing"
    summary = RiskReviewAgent(
        session,
        llm,
        playbook,
        contract_id=contract_id,
        budget=budget,
        batch_size=settings.agent_batch_size,
    ).review(clauses)

    progress.stage = "finishing"
    contract = contracts.get_for_update(contract_id)
    assert contract is not None
    record_agent_event(
        session,
        "agent.completed",
        contract_id,
        {
            "playbook_version": playbook.version,
            "clause_count": len(clauses),
            "clauses_reviewed": summary.clauses_reviewed,
            "clauses_skipped": summary.clauses_skipped,
            "batches": summary.batches,
            "flags_created": summary.flags_created,
            "llm_calls": budget.iterations,
            "total_tokens": budget.tokens,
        },
    )
    _transition(contracts, contract, ContractStatus.AWAITING_REVIEW)
    session.commit()


def _mark_failed(session: Session, contract_id: uuid.UUID, stage: str, exc: Exception) -> None:
    """Record the failure in a fresh transaction. Only the error type is logged, never text."""
    try:
        contracts = ContractRepository(session)
        contract = contracts.get_for_update(contract_id)
        if contract is None or contract.status not in (
            ContractStatus.EXTRACTING,
            ContractStatus.ANALYZING,
        ):
            return
        if isinstance(exc, AgentBudgetExceededError):
            record_agent_event(
                session,
                "agent.stopped",
                contract_id,
                {
                    "reason": exc.reason,
                    "stage": stage,
                    "llm_calls": exc.iterations,
                    "total_tokens": exc.tokens,
                },
            )
        _transition(
            contracts, contract, ContractStatus.FAILED, reason=f"{stage}:{type(exc).__name__}"
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Could not mark contract %s as failed", contract_id)


def _transition(
    contracts: ContractRepository,
    contract: Contract,
    target: ContractStatus,
    reason: str | None = None,
) -> None:
    contracts.transition(
        contract, target, actor_type=ActorType.AGENT, actor=AGENT_ACTOR, reason=reason
    )
