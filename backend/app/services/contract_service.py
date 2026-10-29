import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.models import ActorType, Contract, ContractStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.contract_repository import ContractRepository
from app.services.storage import LocalFileStorage

# No auth yet: uploads are attributed to an anonymous human until users exist.
ANONYMOUS_ACTOR = "anonymous"


class ContractService:
    def __init__(self, session: Session, storage: LocalFileStorage) -> None:
        self.session = session
        self.storage = storage
        self.contracts = ContractRepository(session)
        self.audit = AuditRepository(session)

    def upload(self, filename: str, stream: BinaryIO) -> Contract:
        original_filename = Path(filename).name[:255]
        stored = self.storage.save_contract(original_filename, stream)
        try:
            contract = self.contracts.add(
                Contract(
                    original_filename=original_filename,
                    content_type=stored.content_type,
                    storage_key=stored.storage_key,
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    status=ContractStatus.UPLOADED,
                )
            )
            self.audit.append(
                event_type="contract.uploaded",
                actor_type=ActorType.HUMAN,
                actor=ANONYMOUS_ACTOR,
                contract_id=contract.id,
                payload={
                    "original_filename": original_filename,
                    "content_type": stored.content_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                },
            )
            self.session.commit()
        except BaseException:
            self.session.rollback()
            self.storage.delete(stored.storage_key)
            raise
        return contract

    def get(self, contract_id: uuid.UUID) -> Contract | None:
        return self.contracts.get(contract_id)
