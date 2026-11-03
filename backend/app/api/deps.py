from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agent import AnalysisRunner
from app.core.config import Settings, get_settings
from app.core.db import get_db, get_sessionmaker
from app.llm import LLMConfigurationError, get_llm_provider
from app.services.analysis_service import AnalysisService
from app.services.contract_service import ContractService
from app.services.storage import LocalFileStorage


def get_storage(settings: Settings = Depends(get_settings)) -> LocalFileStorage:
    return LocalFileStorage(root=settings.upload_dir, max_bytes=settings.max_upload_bytes)


def get_contract_service(
    session: Session = Depends(get_db),
    storage: LocalFileStorage = Depends(get_storage),
) -> ContractService:
    return ContractService(session, storage)


def get_analysis_service(session: Session = Depends(get_db)) -> AnalysisService:
    return AnalysisService(session)


def get_analysis_runner(
    settings: Settings = Depends(get_settings),
    storage: LocalFileStorage = Depends(get_storage),
) -> AnalysisRunner:
    """The background analysis job. Resolved before the request changes any state, so a
    misconfigured provider fails the request instead of leaving a contract stuck."""
    try:
        llm = get_llm_provider(settings)
    except LLMConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return AnalysisRunner(
        session_factory=get_sessionmaker(), llm=llm, storage=storage, settings=settings
    )
