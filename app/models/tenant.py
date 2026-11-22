"""Imobiliária (tenant), identidade de usuários e roteamento de canais."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, TenantScoped, Timestamped, UuidPk, check_in
from app.models.enums import MemberRole, TenantStatus, Tone, values


class Tenant(Timestamped, Base):
    """Imobiliária. RLS: cada sessão só enxerga o próprio tenant (`id`)."""

    __tablename__ = "tenant"
    __table_args__ = (
        CheckConstraint(check_in("status", values(TenantStatus)), name="status"),
        CheckConstraint(check_in("tone", values(Tone)), name="tone"),
        CheckConstraint("slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'", name="slug_format"),
    )

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    status: Mapped[str] = mapped_column(String(20), server_default=TenantStatus.ONBOARDING.value)
    paused_at: Mapped[datetime | None]
    """RF19: botão "pausar assistente"; efeito imediato quando preenchido."""
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    assistant_name: Mapped[str] = mapped_column(String(60), server_default="Assistente virtual")
    tone: Mapped[str] = mapped_column(String(20), server_default=Tone.FORMAL.value)
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    broker_hours: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    """RF24: horário dos corretores, por dia da semana."""
    active_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_snapshot.id", ondelete="RESTRICT", use_alter=True),
    )
    availability_updated_at: Mapped[datetime | None]
    """RF13: última confirmação das datas (lembrete, painel, iCal). Mais de 24 h = vencido."""


class TenantRoute(Base):
    """Endereço externo → tenant, consultado antes de saber o tenant (ex.: webhook).

    Tabela global sem RLS e sem segredos: só o identificador público do canal.
    """

    __tablename__ = "tenant_route"
    __table_args__ = (
        UniqueConstraint("channel", "external_id"),
        CheckConstraint(check_in("channel", ("whatsapp", "slug")), name="channel"),
    )

    id: Mapped[UuidPk]
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(64))
    """WhatsApp: `phone_number_id` da Cloud API."""
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[CreatedAt]


class WhatsAppAccount(TenantScoped, Timestamped, Base):
    """Número da imobiliária na Cloud API (via parceiro). Token cifrado pela aplicação."""

    __tablename__ = "whatsapp_account"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_number_id"),)

    id: Mapped[UuidPk]
    phone_number_id: Mapped[str] = mapped_column(String(64))
    display_phone: Mapped[str] = mapped_column(String(20))
    waba_id: Mapped[str | None] = mapped_column(String(64))
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    quality_rating: Mapped[str | None] = mapped_column(String(20))
    is_test_number: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class AppUser(Timestamped, Base):
    """Conta do painel (identidade global). O vínculo com imobiliárias fica em `tenant_member`.

    Login: e-mail + senha + TOTP obrigatório para todos (SPEC, seção 5).
    """

    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[UuidPk]
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(Text)
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_operator: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    failed_logins: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]


class TenantMember(TenantScoped, Timestamped, Base):
    """Papel de um usuário numa imobiliária (gestor ou corretor)."""

    __tablename__ = "tenant_member"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        CheckConstraint(check_in("role", values(MemberRole)), name="role"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    whatsapp_phone: Mapped[str | None] = mapped_column(String(20))
    """Corretor: WhatsApp pessoal para avisos de passagem (só com consentimento)."""
    duty_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """RF29: escala de plantão do corretor (formato do horário da imobiliária); vazio = segue
    o horário da imobiliária."""
    weekly_email_off_at: Mapped[datetime | None]
    """Gestor desligou o relatório semanal por e-mail em "Meus avisos" (sai sem pedir aceite)."""
