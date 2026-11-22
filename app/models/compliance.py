"""Auditoria, aviso de privacidade, consentimentos e relatórios semanais."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, TenantScoped, UuidPk, check_in
from app.models.enums import ConsentPurpose, values


class AuditLog(TenantScoped, Base):
    """Trilha de auditoria: só inclusão, encadeada por hash por tenant.

    `seq`, `prev_hash`, `hash` e `created_at` são preenchidos por gatilho no banco;
    UPDATE e DELETE são recusados (SPEC, seção 7, item 9).
    """

    __tablename__ = "audit_log"
    __table_args__ = (UniqueConstraint("tenant_id", "seq"),)

    id: Mapped[UuidPk]
    seq: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    """Sem chave estrangeira: a trilha sobrevive à anonimização ou exclusão da conversa."""
    event_type: Mapped[str] = mapped_column(String(40))
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[CreatedAt]


class PrivacyNotice(TenantScoped, Base):
    """Aviso de privacidade versionado e imutável por versão (SPEC, seção 9)."""

    __tablename__ = "privacy_notice"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[UuidPk]
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[CreatedAt]


class Consent(TenantScoped, Base):
    """Opt-in de gestor/corretor para mensagens ativas no WhatsApp."""

    __tablename__ = "consent"
    __table_args__ = (
        CheckConstraint(check_in("purpose", values(ConsentPurpose)), name="purpose"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= granted_at", name="revocation_order"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(30))
    granted_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    evidence: Mapped[str] = mapped_column(Text)
    """Como foi dado (ex.: "aceite no painel, versão 1 do texto")."""


class WeeklyReport(TenantScoped, Base):
    """Métricas agregadas da semana, sem dados pessoais (RF18)."""

    __tablename__ = "weekly_report"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_start"),
        CheckConstraint("period_end > period_start", name="period_valid"),
    )

    id: Mapped[UuidPk]
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
