"""initial schema

Creates contracts, clauses, risk_flags, reviews, and audit_events. audit_events is
made append-only by a trigger that rejects UPDATE and DELETE.

Revision ID: 0001
Revises:
Create Date: 2026-10-29 18:39:28.463479

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "contracts",
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded",
                "processing",
                "in_review",
                "completed",
                "failed",
                name="contract_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contracts")),
    )
    op.create_index(op.f("ix_contracts_sha256"), "contracts", ["sha256"], unique=False)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "actor_type",
            sa.Enum(
                "agent",
                "human",
                "system",
                name="actor_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_audit_events_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        op.f("ix_audit_events_contract_id"), "audit_events", ["contract_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_events_event_type"), "audit_events", ["event_type"], unique=False
    )

    # audit_events is append-only: reject any UPDATE or DELETE at the database level.
    # TRUNCATE is intentionally not blocked so an admin can reset a non-production DB.
    op.execute(
        """
        CREATE FUNCTION audit_events_block_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
        """
    )
    op.create_table(
        "clauses",
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("clause_type", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_clauses_contract_id_contracts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clauses")),
        sa.UniqueConstraint("contract_id", "ordinal", name=op.f("uq_clauses_contract_id")),
    )
    op.create_index(op.f("ix_clauses_contract_id"), "clauses", ["contract_id"], unique=False)
    op.create_table(
        "risk_flags",
        sa.Column("clause_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "low",
                "medium",
                "high",
                "critical",
                name="severity",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "edited",
                "rejected",
                name="flag_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name=op.f("ck_risk_flags_score_range")),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["clauses.id"],
            name=op.f("fk_risk_flags_clause_id_clauses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_flags")),
    )
    op.create_index(op.f("ix_risk_flags_clause_id"), "risk_flags", ["clause_id"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("risk_flag_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "approve",
                "edit",
                "reject",
                name="review_decision",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["risk_flag_id"],
            ["risk_flags.id"],
            name=op.f("fk_reviews_risk_flag_id_risk_flags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
    )
    op.create_index(op.f("ix_reviews_risk_flag_id"), "reviews", ["risk_flag_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_reviews_risk_flag_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_risk_flags_clause_id"), table_name="risk_flags")
    op.drop_table("risk_flags")
    op.drop_index(op.f("ix_clauses_contract_id"), table_name="clauses")
    op.drop_table("clauses")
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_mutation()")
    op.drop_index(op.f("ix_audit_events_event_type"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_contract_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_contracts_sha256"), table_name="contracts")
    op.drop_table("contracts")
