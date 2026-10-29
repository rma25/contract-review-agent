from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.services.contract_service import ContractService
from app.services.storage import LocalFileStorage


def get_storage(settings: Settings = Depends(get_settings)) -> LocalFileStorage:
    return LocalFileStorage(root=settings.upload_dir, max_bytes=settings.max_upload_bytes)


def get_contract_service(
    session: Session = Depends(get_db),
    storage: LocalFileStorage = Depends(get_storage),
) -> ContractService:
    return ContractService(session, storage)
