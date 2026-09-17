from sqlalchemy import BigInteger, String, Text, Boolean, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Mandatory Case-Insensitive SKU uniqueness constraint enforced at PostgreSQL level
        Index("uq_products_sku_lower", func.lower(sku), unique=True),
        # Index on active status for fast filtering
        Index("ix_products_active", active),
        # Index on created_at for fast descending pagination
        Index("ix_products_created_at", created_at.desc()),
    )

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, sku='{self.sku}', active={self.active})>"
