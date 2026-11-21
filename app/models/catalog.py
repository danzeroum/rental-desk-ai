"""Dados aprovados pela imobiliária: snapshots, imóveis, tarifas e bloqueios.

Imóveis e tarifas pertencem a um snapshot imutável; o tenant aponta para o
snapshot aprovado em uso (`tenant.active_snapshot_id`). Bloqueios não são
versionados: mudam a qualquer hora (painel, lembrete, iCal) e referenciam o
imóvel pelo código, que é estável entre snapshots.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, TenantScoped, UuidPk, check_in
from app.models.enums import BlockSource, SnapshotSource, SnapshotStatus, values

PROPERTY_CODE_CHECK = "code ~ '^[A-Z0-9][A-Z0-9_-]{0,19}$'"


class DataSnapshot(TenantScoped, Base):
    __tablename__ = "data_snapshot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version"),
        CheckConstraint(check_in("status", values(SnapshotStatus)), name="status"),
        CheckConstraint(check_in("source", values(SnapshotSource)), name="source"),
        CheckConstraint(
            "(status IN ('approved', 'superseded')) = (approved_at IS NOT NULL)",
            name="approval_recorded",
        ),
        CheckConstraint("length(content_hash) = 64", name="content_hash_sha256"),
    )

    id: Mapped[UuidPk]
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default=SnapshotStatus.DRAFT.value)
    content_hash: Mapped[str] = mapped_column(String(64))
    """SHA-256 (hex) do conteúdo normalizado; vai para o audit_log de cada resposta."""
    file_name: Mapped[str | None] = mapped_column(String(255))
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    """Aba Regras: chave/valor curto (check-in, check-out, caução, pagamento, cancelamento)."""
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    """Avisos não bloqueantes da importação (RF14), mostrados no painel."""
    blocks: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    """Aba Bloqueios do arquivo; viram linhas de `block` na aprovação (RF15)."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None]


class Property(TenantScoped, Base):
    """Imóvel numa versão dos dados. Atributo `None` = desconhecido, nunca "não" (RF09)."""

    __tablename__ = "property"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "code"),
        CheckConstraint(PROPERTY_CODE_CHECK, name="code_format"),
        CheckConstraint("max_guests > 0", name="max_guests_positive"),
    )

    id: Mapped[UuidPk]
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshot.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    neighborhood: Mapped[str | None] = mapped_column(String(120))
    photos_url: Mapped[str | None] = mapped_column(Text)
    max_guests: Mapped[int] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    suites: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    parking_spots: Mapped[int | None] = mapped_column(Integer)
    has_pool: Mapped[bool | None] = mapped_column(Boolean)
    accepts_pets: Mapped[bool | None] = mapped_column(Boolean)
    allows_parties: Mapped[bool | None] = mapped_column(Boolean)
    ac_in_bedrooms: Mapped[bool | None] = mapped_column(Boolean)
    has_wifi: Mapped[bool | None] = mapped_column(Boolean)
    has_barbecue: Mapped[bool | None] = mapped_column(Boolean)
    beach_distance_m: Mapped[int | None] = mapped_column(Integer)
    public_notes: Mapped[str | None] = mapped_column(Text)


class Rate(TenantScoped, Base):
    """Tarifa de um período. Dinheiro em centavos (SPEC, seção 6)."""

    __tablename__ = "rate"
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="period_valid"),
        CheckConstraint("daily_cents >= 0", name="daily_non_negative"),
        CheckConstraint("min_nights >= 1", name="min_nights_positive"),
        CheckConstraint("cleaning_fee_cents >= 0", name="cleaning_non_negative"),
        CheckConstraint(
            "(package_cents IS NULL) = (package_name IS NULL)", name="package_complete"
        ),
        CheckConstraint("package_cents IS NULL OR package_cents >= 0", name="package_non_negative"),
        Index("ix_rate_lookup", "snapshot_id", "property_code", "start_date"),
    )

    id: Mapped[UuidPk]
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshot.id", ondelete="CASCADE")
    )
    property_code: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    daily_cents: Mapped[int] = mapped_column(BigInteger)
    min_nights: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    package_name: Mapped[str | None] = mapped_column(String(60))
    package_cents: Mapped[int | None] = mapped_column(BigInteger)
    package_closed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cleaning_fee_cents: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class Block(TenantScoped, Base):
    """Período ocupado. Mesma convenção da estadia: `start_date` ≤ noite < `end_date`."""

    __tablename__ = "block"
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="period_valid"),
        CheckConstraint(check_in("source", values(BlockSource)), name="source"),
        Index("ix_block_lookup", "tenant_id", "property_code", "start_date"),
        Index(
            "uq_block_external",
            "tenant_id",
            "source",
            "external_uid",
            unique=True,
            postgresql_where=text("external_uid IS NOT NULL"),
        ),
    )

    id: Mapped[UuidPk]
    property_code: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20))
    external_uid: Mapped[str | None] = mapped_column(String(255))
    """iCal: UID do evento, para sincronizar sem duplicar (RF26)."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
