"""Hóspedes, conversas, mensagens, extrações, buscas e passagens ao corretor."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
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

from app.models.base import Base, CreatedAt, TenantScoped, Timestamped, UuidPk, check_in
from app.models.enums import (
    ConversationStatus,
    Direction,
    HandoffOutcome,
    HandoffStatus,
    MessageKind,
    MessageOrigin,
    values,
)

INITIAL_STATE_SQL = text(
    """'{"slots": {"check_in": null, "check_out": null, "guests": null,
    "budget_max_cents": null, "neighborhood": null, "required": [], "pets": null},
    "last_offered": [], "pending_questions": [], "clarification_attempts": 0,
    "language": "pt"}'::jsonb"""
)


class Contact(TenantScoped, Timestamped, Base):
    """Hóspede. Só o mínimo (SPEC, seção 9); anonimizado após a retenção."""

    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_e164"),)

    id: Mapped[UuidPk]
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    """`None` depois de anonimizado (RF21 / retenção)."""
    profile_name: Mapped[str | None] = mapped_column(String(120))
    opted_out_at: Mapped[datetime | None]
    """RF21: "PARAR" → sem atendimento automático para este número."""
    anonymized_at: Mapped[datetime | None]


class Conversation(TenantScoped, Timestamped, Base):
    __tablename__ = "conversation"
    __table_args__ = (
        CheckConstraint(check_in("status", values(ConversationStatus)), name="status"),
        Index("ix_conversation_contact", "tenant_id", "contact_id", "last_message_at"),
    )

    id: Mapped[UuidPk]
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), server_default=ConversationStatus.ASSISTANT)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=INITIAL_STATE_SQL)
    muted_until: Mapped[datetime | None]
    """RF11: corretor assumiu; assistente mudo até este instante."""
    privacy_notice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("privacy_notice.id", ondelete="RESTRICT")
    )
    """RF02: versão do aviso de privacidade enviada na 1ª resposta."""
    first_inbound_at: Mapped[datetime | None]
    first_response_at: Mapped[datetime | None]
    last_message_at: Mapped[datetime | None]


class Message(TenantScoped, Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint(check_in("direction", values(Direction)), name="direction"),
        CheckConstraint(check_in("kind", values(MessageKind)), name="kind"),
        CheckConstraint(check_in("origin", values(MessageOrigin)), name="origin"),
        Index(
            "ix_message_unprocessed",
            "conversation_id",
            "created_at",
            postgresql_where=text("direction = 'in' AND processed_at IS NULL"),
        ),
        Index(
            "uq_message_external_id",
            "tenant_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_message_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[UuidPk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE")
    )
    direction: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(128))
    """ID da Cloud API; único por tenant (RF01: repetido é processado uma vez)."""
    body: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    media_ref: Mapped[str | None] = mapped_column(Text)
    """Referência temporária do áudio; apagada após a transcrição (SPEC, seção 9)."""
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    external_timestamp: Mapped[datetime | None]
    origin: Mapped[str] = mapped_column(String(20), server_default=MessageOrigin.GUEST.value)
    processed_at: Mapped[datetime | None]
    """Entrada já tratada pelo pipeline (lote do debounce)."""
    created_at: Mapped[CreatedAt]


class Intent(TenantScoped, Base):
    """JSON da extração (validado contra o esquema) de um lote de mensagens."""

    __tablename__ = "intent"

    id: Mapped[UuidPk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]


class SearchResult(TenantScoped, Base):
    __tablename__ = "search_result"

    id: Mapped[UuidPk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshot.id", ondelete="RESTRICT")
    )
    query: Mapped[dict[str, Any]] = mapped_column(JSONB)
    results: Mapped[list[Any]] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]


class Handoff(TenantScoped, Timestamped, Base):
    """Passagem ao corretor (RF10-RF12, RF17, RF29)."""

    __tablename__ = "handoff"
    __table_args__ = (
        CheckConstraint(check_in("status", values(HandoffStatus)), name="status"),
        CheckConstraint(
            f"outcome IS NULL OR {check_in('outcome', values(HandoffOutcome))}", name="outcome"
        ),
        CheckConstraint("status <> 'assumed' OR assumed_at IS NOT NULL", name="assumed_recorded"),
        Index("ix_handoff_queue", "tenant_id", "status", "created_at"),
    )

    id: Mapped[UuidPk]
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(40))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default=HandoffStatus.OPEN.value)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None]
    assumed_at: Mapped[datetime | None]
    assumed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    outcome: Mapped[str | None] = mapped_column(String(20))
    outcome_at: Mapped[datetime | None]
    outcome_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
