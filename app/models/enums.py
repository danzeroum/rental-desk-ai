"""Valores fechados guardados como texto com CHECK (ver DECISIONS, D012)."""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ONBOARDING = "onboarding"  # assistente de implantação em andamento (RF28)
    SHADOW = "shadow"  # modo sombra: grava a resposta que daria, sem enviar
    ACTIVE = "active"
    SUSPENDED = "suspended"  # contrato suspenso pelo operador


class Tone(StrEnum):
    """RF24: um de dois tons."""

    FORMAL = "formal"
    CASUAL = "casual"


class MemberRole(StrEnum):
    GESTOR = "gestor"
    CORRETOR = "corretor"


class SnapshotSource(StrEnum):
    UPLOAD = "upload"
    GOOGLE_SHEET = "google_sheet"


class SnapshotStatus(StrEnum):
    DRAFT = "draft"  # importado, aguardando aprovação
    APPROVED = "approved"  # em uso
    SUPERSEDED = "superseded"  # foi aprovado, depois substituído
    REJECTED = "rejected"


class BlockSource(StrEnum):
    SPREADSHEET = "spreadsheet"
    PANEL = "panel"
    REMINDER = "reminder"  # resposta ao lembrete das 9h (RF16)
    ICAL = "ical"
    GOOGLE_SHEET = "google_sheet"


class ConversationStatus(StrEnum):
    ASSISTANT = "assistant"
    HUMAN = "human"
    CLOSED = "closed"


class Direction(StrEnum):
    INBOUND = "in"
    OUTBOUND = "out"


class MessageOrigin(StrEnum):
    GUEST = "guest"
    ASSISTANT = "assistant"
    BUSINESS_APP = "business_app"  # eco do app WhatsApp Business (coexistência)
    PANEL = "panel"


class MessageKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    TEMPLATE = "template"
    OTHER = "other"


class HandoffStatus(StrEnum):
    OPEN = "open"
    ASSUMED = "assumed"
    CLOSED = "closed"


class HandoffOutcome(StrEnum):
    """RF17: fechou / perdeu / pendente."""

    WON = "won"
    LOST = "lost"
    PENDING = "pending"


class ConsentPurpose(StrEnum):
    """Mensagens ativas que exigem consentimento registrado (SPEC, seção 9)."""

    HANDOFF_ALERT = "handoff_alert"
    DAILY_REMINDER = "daily_reminder"
    WEEKLY_REPORT = "weekly_report"


def values(enum: type[StrEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum)
