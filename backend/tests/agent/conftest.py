from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.agent import AnalysisRunner
from app.core.config import Settings
from app.llm.audited import AuditedLLMProvider
from app.llm.retry import RetryPolicy
from app.models import Clause, Contract, ContractStatus
from app.services.storage import DOCX_CONTENT_TYPE, LocalFileStorage
from tests.agent.fake_llm import FakeLLMProvider
from tests.contract_fixtures import HIGH_RISK, fixture_docx

MakeContract = Callable[..., Contract]
MakeRunner = Callable[..., AnalysisRunner]


@pytest.fixture
def agent_settings(upload_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        upload_dir=upload_dir,
        llm_provider="gemini",
        agent_batch_size=5,
        agent_max_iterations=25,
        agent_token_budget=150_000,
        agent_max_contract_chars=60_000,
    )


@pytest.fixture
def storage(agent_settings: Settings) -> LocalFileStorage:
    return LocalFileStorage(agent_settings.upload_dir, agent_settings.max_upload_bytes)


@pytest.fixture
def audited(
    session_factory: Callable[[], Session],
) -> Callable[[FakeLLMProvider], AuditedLLMProvider]:
    """Wrap a fake in the real audited provider (no retries, no sleeping)."""

    def wrap(fake: FakeLLMProvider) -> AuditedLLMProvider:
        return AuditedLLMProvider(
            fake,
            session_factory=session_factory,
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=lambda _: None,
        )

    return wrap


@pytest.fixture
def make_runner(
    session_factory: Callable[[], Session],
    storage: LocalFileStorage,
    agent_settings: Settings,
    audited: Callable[[FakeLLMProvider], AuditedLLMProvider],
) -> MakeRunner:
    def make(fake: FakeLLMProvider, **setting_overrides: object) -> AnalysisRunner:
        settings = agent_settings.model_copy(update=setting_overrides)
        return AnalysisRunner(
            session_factory=session_factory, llm=audited(fake), storage=storage, settings=settings
        )

    return make


@pytest.fixture
def make_contract(db_session: Session, storage: LocalFileStorage) -> MakeContract:
    """Store a fixture as a DOCX and insert its contract row (committed)."""

    def make(
        fixture: str = HIGH_RISK,
        status: ContractStatus = ContractStatus.EXTRACTING,
        content: bytes | None = None,
    ) -> Contract:
        storage.root.mkdir(parents=True, exist_ok=True)
        data = fixture_docx(fixture) if content is None else content
        key = f"{fixture}.docx"
        storage.path_for(key).write_bytes(data)
        contract = Contract(
            original_filename=key,
            content_type=DOCX_CONTENT_TYPE,
            storage_key=key,
            size_bytes=len(data),
            sha256="0" * 64,
            status=status,
        )
        db_session.add(contract)
        db_session.commit()
        return contract

    return make


@pytest.fixture
def add_clauses(db_session: Session) -> Callable[..., list[Clause]]:
    def add(contract: Contract, *specs: tuple[str, str]) -> list[Clause]:
        clauses = [
            Clause(contract_id=contract.id, ordinal=i, clause_type=clause_type, text=text)
            for i, (clause_type, text) in enumerate(specs, start=1)
        ]
        db_session.add_all(clauses)
        db_session.commit()
        return clauses

    return add
