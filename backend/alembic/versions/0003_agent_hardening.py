"""agent hardening

Adds organizations (seeded with a default org) and contracts.organization_id, so the
agent's similar-clause search can be scoped to one tenant. Adds clauses.agent_outcome and
clauses.agent_reason for the agent's per-clause review decision.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match app.models.organization.DEFAULT_ORGANIZATION_ID.
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.execute(
        f"INSERT INTO organizations (id, name) VALUES ('{DEFAULT_ORGANIZATION_ID}', 'Default')"
    )

    op.add_column("contracts", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.execute(f"UPDATE contracts SET organization_id = '{DEFAULT_ORGANIZATION_ID}'")
    op.alter_column("contracts", "organization_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_contracts_organization_id_organizations"),
        "contracts",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_contracts_organization_id"), "contracts", ["organization_id"], unique=False
    )

    op.add_column("clauses", sa.Column("agent_outcome", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        op.f("ck_clauses_clause_outcome"),
        "clauses",
        "agent_outcome IN ('flagged', 'no_issue')",
    )
    op.add_column("clauses", sa.Column("agent_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("clauses", "agent_reason")
    op.drop_constraint(op.f("ck_clauses_clause_outcome"), "clauses", type_="check")
    op.drop_column("clauses", "agent_outcome")

    op.drop_index(op.f("ix_contracts_organization_id"), table_name="contracts")
    op.drop_constraint(
        op.f("fk_contracts_organization_id_organizations"), "contracts", type_="foreignkey"
    )
    op.drop_column("contracts", "organization_id")
    op.drop_table("organizations")
