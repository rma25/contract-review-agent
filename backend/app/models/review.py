import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReviewDecision, str_enum

if TYPE_CHECKING:
    from app.models.risk_flag import RiskFlag


class Review(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A human decision on a risk flag."""

    __tablename__ = "reviews"

    risk_flag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_flags.id", ondelete="CASCADE"), index=True
    )
    reviewer: Mapped[str] = mapped_column(String(255))
    decision: Mapped[ReviewDecision] = mapped_column(str_enum(ReviewDecision, "review_decision"))
    edited_text: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)

    risk_flag: Mapped["RiskFlag"] = relationship(back_populates="reviews")
