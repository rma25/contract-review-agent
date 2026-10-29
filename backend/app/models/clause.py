import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.contract import Contract
    from app.models.risk_flag import RiskFlag


class Clause(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "clauses"
    __table_args__ = (UniqueConstraint("contract_id", "ordinal"),)

    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    clause_type: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)

    contract: Mapped["Contract"] = relationship(back_populates="clauses")
    risk_flags: Mapped[list["RiskFlag"]] = relationship(
        back_populates="clause", cascade="all, delete-orphan"
    )
