"""agent pipeline

Adds contracts.raw_text, clauses.heading, and a generated full-text search vector on
clauses. Replaces the contract status values with the agent pipeline's states and makes
(clause_id, rule_id) unique on risk_flags.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_VECTOR_SQL = "to_tsvector('english', coalesce(heading, '') || ' ' || text)"

OLD_STATUSES = ("uploaded", "processing", "in_review", "completed", "failed")
NEW_STATUSES = ("uploaded", "extracting", "analyzing", "awaiting_review", "completed", "failed")


def _status_check(values: Sequence[str]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("contracts", sa.Column("raw_text", sa.Text(), nullable=True))

    op.drop_constraint(op.f("ck_contracts_contract_status"), "contracts", type_="check")
    op.execute("UPDATE contracts SET status = 'extracting' WHERE status = 'processing'")
    op.execute("UPDATE contracts SET status = 'awaiting_review' WHERE status = 'in_review'")
    op.create_check_constraint(
        op.f("ck_contracts_contract_status"), "contracts", _status_check(NEW_STATUSES)
    )

    op.add_column("clauses", sa.Column("heading", sa.String(length=512), nullable=True))
    op.add_column(
        "clauses",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_clauses_search_vector",
        "clauses",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_unique_constraint(
        op.f("uq_risk_flags_clause_id"), "risk_flags", ["clause_id", "rule_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_risk_flags_clause_id"), "risk_flags", type_="unique")
    op.drop_index("ix_clauses_search_vector", table_name="clauses", postgresql_using="gin")
    op.drop_column("clauses", "search_vector")
    op.drop_column("clauses", "heading")

    op.drop_constraint(op.f("ck_contracts_contract_status"), "contracts", type_="check")
    op.execute(
        "UPDATE contracts SET status = 'processing' WHERE status IN ('extracting', 'analyzing')"
    )
    op.execute("UPDATE contracts SET status = 'in_review' WHERE status = 'awaiting_review'")
    op.create_check_constraint(
        op.f("ck_contracts_contract_status"), "contracts", _status_check(OLD_STATUSES)
    )
    op.drop_column("contracts", "raw_text")
