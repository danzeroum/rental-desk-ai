"""Quem pode receber avisos ativos: corretores e gestores com WhatsApp e consentimento.

Sem consentimento registrado (e não revogado) para avisos de passagem, a pessoa não
recebe mensagem ativa (CLAUDE.md, "Não faça"; SPEC, seção 9).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.hours import BusinessHours, InvalidHoursError
from app.domain.rotation import Broker
from app.models import AppUser, Consent, Contact, Handoff, TenantMember
from app.models.enums import ConsentPurpose, MemberRole

log = get_logger(__name__)


def _with_consent(  # type: ignore[no-untyped-def]
    role: MemberRole, purpose: ConsentPurpose = ConsentPurpose.HANDOFF_ALERT
):
    consented = exists().where(
        and_(
            Consent.user_id == TenantMember.user_id,
            Consent.phone_e164 == TenantMember.whatsapp_phone,
            Consent.purpose == purpose,
            Consent.revoked_at.is_(None),
        )
    )
    opted_out = exists().where(
        and_(
            Contact.phone_e164 == TenantMember.whatsapp_phone,
            Contact.opted_out_at.is_not(None),
        )
    )
    return (
        select(TenantMember.user_id, AppUser.display_name, TenantMember.whatsapp_phone)
        .join(AppUser, AppUser.id == TenantMember.user_id)
        .where(
            TenantMember.role == role,
            TenantMember.whatsapp_phone.is_not(None),
            AppUser.is_active.is_(True),
            consented,
            ~opted_out,  # PARAR: nenhum template para o número (RF21)
        )
        .order_by(TenantMember.created_at, TenantMember.user_id)
    )


async def brokers_on_duty(session: AsyncSession, now: datetime) -> list[Broker]:
    """RF29: corretores com aviso aceito e de plantão agora (escala própria ou, sem escala, o
    horário da imobiliária, conferido por quem chama)."""
    query = _with_consent(MemberRole.CORRETOR).add_columns(TenantMember.duty_hours)
    rows = (await session.execute(query)).all()
    return [
        Broker(r.user_id, r.display_name, r.whatsapp_phone)
        for r in rows
        if on_duty(r.duty_hours, now)
    ]


def on_duty(duty_hours: dict[str, Any] | None, now: datetime) -> bool:
    if not duty_hours:
        return True
    try:
        return BusinessHours.from_json(duty_hours).is_open(now)
    except InvalidHoursError:
        log.warning("escala_de_plantao_invalida")
        return True  # escala quebrada não pode deixar a imobiliária sem ninguém


async def managers_to_copy(session: AsyncSession) -> list[Broker]:
    rows = (await session.execute(_with_consent(MemberRole.GESTOR))).all()
    return [Broker(r.user_id, r.display_name, r.whatsapp_phone) for r in rows]


async def last_assignments(session: AsyncSession) -> dict[uuid.UUID, datetime | None]:
    rows = (
        await session.execute(
            select(Handoff.assigned_to, func.max(Handoff.assigned_at))
            .where(Handoff.assigned_to.is_not(None))
            .group_by(Handoff.assigned_to)
        )
    ).all()
    return {user: at for user, at in rows if user is not None}


async def managers_with_consent(session: AsyncSession, purpose: ConsentPurpose) -> list[Broker]:
    """Gestores que aceitaram a finalidade (lembrete diário, relatório) no próprio painel."""
    rows = (await session.execute(_with_consent(MemberRole.GESTOR, purpose))).all()
    return [Broker(r.user_id, r.display_name, r.whatsapp_phone) for r in rows]


async def member_by_phone(session: AsyncSession, phone_e164: str) -> TenantMember | None:
    """Corretor ou gestor que escreveu para o número da imobiliária (ex.: "1")."""
    member: TenantMember | None = await session.scalar(
        select(TenantMember).where(TenantMember.whatsapp_phone == phone_e164).limit(1)
    )
    return member


async def operator_emails(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(AppUser.email).where(AppUser.is_operator.is_(True), AppUser.is_active.is_(True))
    )
    return list(rows.scalars())


async def manager_emails(session: AsyncSession, *, weekly_report: bool = False) -> list[str]:
    """E-mails dos gestores ativos (com `weekly_report`, só quem não desligou o relatório)."""
    query = (
        select(AppUser.email)
        .join(TenantMember, TenantMember.user_id == AppUser.id)
        .where(TenantMember.role == MemberRole.GESTOR, AppUser.is_active.is_(True))
        .order_by(AppUser.email)
    )
    if weekly_report:
        query = query.where(
            TenantMember.weekly_email_off_at.is_(None), AppUser.password_hash.is_not(None)
        )
    return list((await session.execute(query)).scalars())
