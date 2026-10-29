import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ContractStatus


class ClauseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    clause_type: str
    text: str
    page_number: int | None


class ContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: ContractStatus
    created_at: datetime
    updated_at: datetime
    clauses: list[ClauseRead]
