import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import RiskFlag
from app.models.enums import ContractStatus, FlagStatus, Severity


class ClauseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    clause_type: str
    heading: str | None
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


class RiskFlagRead(BaseModel):
    id: uuid.UUID
    clause_id: uuid.UUID
    clause_ordinal: int
    clause_type: str
    clause_heading: str | None
    rule_id: str
    severity: Severity
    score: float
    rationale: str
    status: FlagStatus
    created_at: datetime

    @classmethod
    def from_flag(cls, flag: RiskFlag) -> "RiskFlagRead":
        return cls(
            id=flag.id,
            clause_id=flag.clause_id,
            clause_ordinal=flag.clause.ordinal,
            clause_type=flag.clause.clause_type,
            clause_heading=flag.clause.heading,
            rule_id=flag.rule_id,
            severity=flag.severity,
            score=flag.score,
            rationale=flag.rationale,
            status=flag.status,
            created_at=flag.created_at,
        )
