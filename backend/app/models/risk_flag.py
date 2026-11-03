import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import FlagStatus, Severity, str_enum

if TYPE_CHECKING:
    from app.models.clause import Clause
    from app.models.review import Review


class RiskFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_flags"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        # One flag per playbook rule per clause.
        UniqueConstraint("clause_id", "rule_id"),
    )

    clause_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str] = mapped_column(String(128))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "severity"))
    score: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[FlagStatus] = mapped_column(
        str_enum(FlagStatus, "flag_status"), default=FlagStatus.PENDING
    )

    clause: Mapped["Clause"] = relationship(back_populates="risk_flags")
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="risk_flag", cascade="all, delete-orphan"
    )
