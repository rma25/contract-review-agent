from enum import StrEnum

from sqlalchemy import Enum


class ContractStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    ANALYZING = "analyzing"
    AWAITING_REVIEW = "awaiting_review"
    # Reserved for when a human has resolved every flag. The agent never sets it.
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FlagStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ActorType(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store an enum as VARCHAR + CHECK constraint (not a native PG enum) for easy migrations."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )
