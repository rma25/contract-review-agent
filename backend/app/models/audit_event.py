import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, String, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin
from app.models.enums import ActorType, str_enum


class AuditEventImmutableError(RuntimeError):
    pass


class AuditEvent(CreatedAtMixin, Base):
    """Append-only record of an agent, human, or system step.

    Rows are never updated or deleted. This is enforced by ORM listeners below and by a
    database trigger created in the initial migration.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # RESTRICT: a contract with audit history cannot be deleted.
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    actor: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


@event.listens_for(AuditEvent, "before_update")
def _block_update(*_: object) -> None:
    raise AuditEventImmutableError("audit_events is append-only; updates are not allowed")


@event.listens_for(AuditEvent, "before_delete")
def _block_delete(*_: object) -> None:
    raise AuditEventImmutableError("audit_events is append-only; deletes are not allowed")
