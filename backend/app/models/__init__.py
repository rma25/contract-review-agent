"""Import every model here so Base.metadata is complete for Alembic and relationships."""

from app.models.audit_event import AuditEvent, AuditEventImmutableError
from app.models.base import Base
from app.models.clause import Clause
from app.models.contract import Contract
from app.models.enums import (
    ActorType,
    ClauseOutcome,
    ContractStatus,
    FlagStatus,
    ReviewDecision,
    Severity,
)
from app.models.organization import DEFAULT_ORGANIZATION_ID, Organization
from app.models.review import Review
from app.models.risk_flag import RiskFlag

__all__ = [
    "ActorType",
    "AuditEvent",
    "AuditEventImmutableError",
    "Base",
    "Clause",
    "ClauseOutcome",
    "Contract",
    "ContractStatus",
    "DEFAULT_ORGANIZATION_ID",
    "FlagStatus",
    "Organization",
    "Review",
    "ReviewDecision",
    "RiskFlag",
    "Severity",
]
