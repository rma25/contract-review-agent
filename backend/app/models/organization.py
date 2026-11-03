import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# Seeded by migration 0003. Every contract belongs to it until auth and tenants exist.
DEFAULT_ORGANIZATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Organization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A tenant. Contracts, and everything the agent can search, are scoped to one."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255))
