from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ContractStatus, str_enum

if TYPE_CHECKING:
    from app.models.clause import Clause


class Contract(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "contracts"

    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    # Location relative to UPLOAD_DIR locally; becomes the S3 object key on deploy.
    storage_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[ContractStatus] = mapped_column(
        str_enum(ContractStatus, "contract_status"), default=ContractStatus.UPLOADED
    )

    clauses: Mapped[list["Clause"]] = relationship(
        back_populates="contract", order_by="Clause.ordinal", cascade="all, delete-orphan"
    )
