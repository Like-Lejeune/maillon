"""Modèles SQLAlchemy.

La structure des tables est définie par les migrations (server/migrations),
elles-mêmes tirées de docs/schema.sql. Ces classes ne font que la décrire :
elles ne créent ni ne modifient aucune table.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


USER_ROLES = ("client", "provider", "admin")
USER_STATUSES = ("active", "suspended")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Enum(*USER_ROLES, name="user_role", create_type=False))
    status: Mapped[str] = mapped_column(
        Enum(*USER_STATUSES, name="user_status", create_type=False),
        server_default="active",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Balance(Base):
    __tablename__ = "balances"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    available: Mapped[int] = mapped_column(BigInteger, server_default="0")
    pending: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text, unique=True)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, server_default="60")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


MACHINE_STATUSES = ("pending", "online", "offline", "suspended")
MACHINE_TIERS = ("A", "B", "C")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    enrollment_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(
        Enum(*MACHINE_STATUSES, name="machine_status", create_type=False), server_default="pending"
    )
    tier: Mapped[str | None] = mapped_column(
        Enum(*MACHINE_TIERS, name="machine_tier", create_type=False)
    )
    os: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    gpu_model: Mapped[str | None] = mapped_column(Text)
    vram_mb: Mapped[int | None] = mapped_column(Integer)
    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    ram_mb: Mapped[int | None] = mapped_column(Integer)
    disk_free_gb: Mapped[int | None] = mapped_column(Integer)
    benchmark_tokens_per_sec: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    reliability_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default="1.000")
    jobs_succeeded: Mapped[int] = mapped_column(Integer, server_default="0")
    jobs_failed: Mapped[int] = mapped_column(Integer, server_default="0")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    min_vram_mb: Mapped[int] = mapped_column(Integer)
    context_length: Mapped[int] = mapped_column(Integer)
    file_url: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    price_input_per_mtok: Mapped[int] = mapped_column(BigInteger)
    price_output_per_mtok: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
