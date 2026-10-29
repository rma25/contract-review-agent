"""Import every model here so Base.metadata is complete for Alembic and relationships."""

from app.models.audit_event import AuditEvent, AuditEventImmutableError
from app.models.base import Base
from app.models.clause import Clause
from app.models.contract import Contract
from app.models.enums import ActorType, ContractStatus, FlagStatus, ReviewDecision, Severity
from app.models.review import Review
from app.models.risk_flag import RiskFlag

__all__ = [
    "ActorType",
    "AuditEvent",
    "AuditEventImmutableError",
    "Base",
    "Clause",
    "Contract",
    "ContractStatus",
    "FlagStatus",
    "Review",
    "ReviewDecision",
    "RiskFlag",
    "Severity",
]
