import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import ClauseOutcome, str_enum

if TYPE_CHECKING:
    from app.models.contract import Contract
    from app.models.risk_flag import RiskFlag


SEARCH_VECTOR_SQL = "to_tsvector('english', coalesce(heading, '') || ' ' || text)"


class Clause(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "clauses"
    __table_args__ = (
        UniqueConstraint("contract_id", "ordinal"),
        Index("ix_clauses_search_vector", "search_vector", postgresql_using="gin"),
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    clause_type: Mapped[str] = mapped_column(String(64))
    heading: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    # The agent's decision from record_clause_reviewed, with its reason for the reviewer.
    # Null for clauses the agent did not review (no playbook rule applies).
    agent_outcome: Mapped[ClauseOutcome | None] = mapped_column(
        str_enum(ClauseOutcome, "clause_outcome")
    )
    agent_reason: Mapped[str | None] = mapped_column(Text)
    # Full-text search over heading + text, used by the agent's search_similar_clauses tool.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_VECTOR_SQL, persisted=True),
        deferred=True,
    )

    contract: Mapped["Contract"] = relationship(back_populates="clauses")
    risk_flags: Mapped[list["RiskFlag"]] = relationship(
        back_populates="clause", cascade="all, delete-orphan"
    )
