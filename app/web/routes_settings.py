"""Configuração da imobiliária (RF24), membros e consentimento para avisos ativos.

O consentimento para mensagens ativas no WhatsApp é dado pela própria pessoa, na
página "Meus avisos", com o texto versionado como evidência (SPEC, seção 9). O
gestor vê a situação, mas não marca por ninguém.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, update

from app.core.audit import append_audit
from app.core.rls import global_session
from app.domain.hours import WEEKDAYS, BusinessHours, InvalidHoursError
from app.domain.phones import InvalidPhoneError, to_e164
from app.models import AppUser, Consent, Contact, Tenant, TenantMember
from app.models.enums import ConsentPurpose, MemberRole, Tone
from app.web.common import auth, redirect, render, verify_csrf
from app.web.context import ManagerCtx, TenantContext, TenantCtx
from app.workers.handoffs import hours_of

router = APIRouter(prefix="/painel")
MAX_NAME: Final = 60
CONSENT_TEXT_VERSION: Final = 1
CONSENT_EVIDENCE: Final = f"aceite no painel pelo próprio usuário, texto v{CONSENT_TEXT_VERSION}"
PURPOSE_LABELS: Final = {
    ConsentPurpose.HANDOFF_ALERT.value: "Avisos de passagem de hóspede (com resumo do pedido)",
    ConsentPurpose.DAILY_REMINDER.value: "Lembrete diário das 9h para confirmar as datas",
    ConsentPurpose.WEEKLY_REPORT.value: "Resumo semanal de resultados",
}
MANAGER_PURPOSES: Final = frozenset(
    {ConsentPurpose.DAILY_REMINDER.value, ConsentPurpose.WEEKLY_REPORT.value}
)


class FormError(ValueError):
    pass


# --- Configurações (RF24) -----------------------------------------------------------------


def _hours_rows(hours: BusinessHours) -> list[dict[str, str]]:
    rows = []
    for day, name in enumerate(WEEKDAYS):
        shifts = hours.shifts[day]
        start = shifts[0][0].strftime("%H:%M") if shifts else ""
        end = shifts[-1][1].strftime("%H:%M") if shifts else ""
        rows.append({"day": str(day), "name": name, "start": start, "end": end})
    return rows


def _parse_hours(form: dict[str, Any]) -> BusinessHours:
    raw: dict[str, list[list[str]]] = {}
    for day in range(7):
        start, end = str(form.get(f"d{day}_inicio", "")), str(form.get(f"d{day}_fim", ""))
        if not start and not end:
            continue
        if not start or not end:
            raise FormError(f"{WEEKDAYS[day]}: preencha início e fim, ou deixe os dois vazios")
        raw[str(day)] = [[start, end]]
    if not raw:
        raise FormError("informe o horário de pelo menos um dia")
    try:
        return BusinessHours.from_json(raw)
    except InvalidHoursError as exc:
        raise FormError(str(exc)) from exc


async def _settings_context(ctx: TenantContext) -> dict[str, Any]:
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        members = (
            await session.execute(
                select(TenantMember, AppUser)
                .join(AppUser, AppUser.id == TenantMember.user_id)
                .order_by(TenantMember.role, AppUser.display_name)
            )
        ).all()
        stopped = set(
            (
                await session.execute(
                    select(Contact.phone_e164).where(
                        Contact.opted_out_at.is_not(None),
                        Contact.phone_e164.in_([m.whatsapp_phone for m, _ in members]),
                    )
                )
            ).scalars()
        )
        consents = (
            await session.execute(select(Consent).where(Consent.revoked_at.is_(None)))
        ).scalars()
        active: dict[uuid.UUID, set[str]] = {}
        for consent in consents:
            active.setdefault(consent.user_id, set()).add(consent.purpose)
    return {
        "tenant": tenant,
        "hours": _hours_rows(hours_of(tenant)),
        "tones": [(Tone.FORMAL.value, "Formal"), (Tone.CASUAL.value, "Descontraído")],
        "members": [
            {
                "id": m.user_id,
                "name": u.display_name,
                "email": u.email,
                "role": m.role,
                "phone": m.whatsapp_phone,
                "active": u.is_active and bool(u.password_hash),
                "alerts": ConsentPurpose.HANDOFF_ALERT.value in active.get(m.user_id, set()),
                "stopped": m.whatsapp_phone is not None and m.whatsapp_phone in stopped,
            }
            for m, u in members
        ],
        "ctx": ctx,
    }


@router.get("/configuracoes", response_class=HTMLResponse)
async def settings_page(request: Request, ctx: ManagerCtx) -> Response:
    return render(request, "panel/settings.html", await _settings_context(ctx))


async def _settings_error(request: Request, ctx: TenantContext, message: str) -> Response:
    values = await _settings_context(ctx)
    return render(request, "panel/settings.html", {**values, "error": message}, status_code=400)


@router.post("/configuracoes")
async def save_settings(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    try:
        name = str(form.get("assistant_name", "")).strip()
        if not name or len(name) > MAX_NAME:
            raise FormError(f"o nome do assistente precisa ter de 1 a {MAX_NAME} caracteres")
        tone = Tone(str(form.get("tone", "")))
        phone_raw = str(form.get("contact_phone", "")).strip()
        phone = to_e164(phone_raw) if phone_raw else None
        hours = _parse_hours(form)
    except (FormError, InvalidPhoneError) as exc:
        return await _settings_error(request, ctx, f"Não salvei: {exc}.")
    except ValueError:
        return await _settings_error(request, ctx, "Não salvei: escolha um dos dois tons.")
    async with ctx.db() as session:
        await session.execute(
            update(Tenant)
            .where(Tenant.id == ctx.tenant_id)
            .values(
                assistant_name=name, tone=tone, contact_phone=phone, broker_hours=hours.to_json()
            )
        )
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="settings_changed",
            payload={"user_id": str(ctx.user_id), "assistant_name": name, "tone": str(tone),
                     "broker_hours": hours.to_json(), "contact_phone_set": phone is not None},
        )  # fmt: skip
    return redirect("/painel/configuracoes")


# --- Membros ----------------------------------------------------------------------------


@router.post("/membros")
async def invite_member(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    email = str(form.get("email", "")).strip().lower()
    name = str(form.get("name", "")).strip()[:120]
    try:
        role = MemberRole(str(form.get("role", "")))
    except ValueError:
        return await _settings_error(request, ctx, "Escolha gestor ou corretor.")
    if "@" not in email or not name:
        return await _settings_error(request, ctx, "Informe nome e e-mail.")
    service = auth(request)
    async with global_session(request.app.state.sessions) as session:
        user = await session.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(email=email, display_name=name)
            session.add(user)
            await session.flush()
    async with ctx.db() as session:
        exists = await session.scalar(select(TenantMember).where(TenantMember.user_id == user.id))
        if exists is not None:
            return await _settings_error(request, ctx, f"{email} já faz parte da equipe.")
        session.add(TenantMember(tenant_id=ctx.tenant_id, user_id=user.id, role=role))
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="member_added",
            payload={"user_id": str(user.id), "role": str(role), "by": str(ctx.user_id)},
        )  # fmt: skip
    if user.password_hash:
        await service.notify_added(user, ctx.panel.tenant_name or "")
    else:
        await service.send_invite(user, ctx.panel.tenant_name)
    return redirect("/painel/configuracoes")


@router.post("/membros/{user_id}/remover")
async def remove_member(request: Request, user_id: uuid.UUID, ctx: ManagerCtx) -> Response:
    await verify_csrf(request)
    if user_id == ctx.user_id:
        return await _settings_error(request, ctx, "Você não pode remover a si mesmo.")
    async with ctx.db() as session:
        member = await session.scalar(select(TenantMember).where(TenantMember.user_id == user_id))
        if member is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        await session.delete(member)
        await session.execute(
            update(Consent)
            .where(Consent.user_id == user_id, Consent.revoked_at.is_(None))
            .values(revoked_at=ctx.now())
        )
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="member_removed",
            payload={"user_id": str(user_id), "by": str(ctx.user_id)},
        )  # fmt: skip
    await auth(request).end_all_sessions(user_id)
    return redirect("/painel/configuracoes")


# --- Meus avisos (consentimento da própria pessoa) -----------------------------------------


def _purposes_for(role: str | None) -> list[str]:
    return [p for p in PURPOSE_LABELS if p not in MANAGER_PURPOSES or role == MemberRole.GESTOR]


@router.get("/avisos", response_class=HTMLResponse)
async def my_alerts(request: Request, ctx: TenantCtx, error: str | None = None) -> Response:
    async with ctx.db() as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == ctx.user_id)
        )
        granted = set(
            (
                await session.execute(
                    select(Consent.purpose).where(
                        Consent.user_id == ctx.user_id, Consent.revoked_at.is_(None)
                    )
                )
            ).scalars()
        )
    return render(
        request, "panel/alerts.html",
        {
            "member": member, "granted": granted, "error": error,
            "is_gestor": ctx.panel.role == MemberRole.GESTOR,
            "purposes": [(p, PURPOSE_LABELS[p]) for p in _purposes_for(ctx.panel.role)],
        },
        status_code=400 if error else 200,
    )  # fmt: skip


@router.post("/avisos")
async def save_my_alerts(request: Request, ctx: TenantCtx) -> Response:
    form = await verify_csrf(request)
    raw = str(form.get("phone", "")).strip()
    try:
        phone = to_e164(raw) if raw else None
    except InvalidPhoneError as exc:
        return await my_alerts(request, ctx, error=f"Telefone: {exc}.")
    await _set_weekly_email(ctx, wants=form.get("weekly_email") == "1")
    wanted = {p for p in _purposes_for(ctx.panel.role) if form.get(p)} if phone else set()
    if wanted and form.get("aceite") != "sim":
        return await my_alerts(request, ctx, error="Marque a concordância para receber avisos.")
    await _apply_consents(ctx, phone, wanted)
    return redirect("/painel/avisos")


async def _set_weekly_email(ctx: TenantContext, *, wants: bool) -> None:
    """Relatório semanal por e-mail: comunicação do serviço; o gestor desliga quando quiser."""
    if ctx.panel.role != MemberRole.GESTOR:
        return
    async with ctx.db() as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == ctx.user_id).with_for_update()
        )
        if member is None or (member.weekly_email_off_at is None) == wants:
            return
        member.weekly_email_off_at = None if wants else ctx.now()
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="weekly_email_changed",
            payload={"user_id": str(ctx.user_id), "enabled": wants, "at": ctx.now().isoformat()},
        )  # fmt: skip


async def _apply_consents(ctx: TenantContext, phone: str | None, wanted: set[str]) -> None:
    now = ctx.now()
    async with ctx.db() as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == ctx.user_id).with_for_update()
        )
        if member is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Só membros da equipe recebem avisos.")
        member.whatsapp_phone = phone
        current = list(
            (
                await session.execute(
                    select(Consent).where(
                        Consent.user_id == ctx.user_id, Consent.revoked_at.is_(None)
                    )
                )
            ).scalars()
        )
        for consent in current:  # telefone trocado ou finalidade desmarcada: revoga
            if consent.phone_e164 != phone or consent.purpose not in wanted:
                consent.revoked_at = now
        kept = {c.purpose for c in current if c.phone_e164 == phone and c.purpose in wanted}
        for purpose in sorted(wanted - kept):
            session.add(
                Consent(
                    tenant_id=ctx.tenant_id, user_id=ctx.user_id, phone_e164=phone or "",
                    purpose=purpose, granted_at=now,
                    evidence=CONSENT_EVIDENCE,
                )
            )  # fmt: skip
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="consent_changed",
            payload={"user_id": str(ctx.user_id), "purposes": sorted(wanted),
                     "text_version": CONSENT_TEXT_VERSION},
        )  # fmt: skip


# --- Escala de plantão (RF29) ------------------------------------------------------------


async def _duty_member(ctx: TenantContext, user_id: uuid.UUID) -> tuple[TenantMember, AppUser]:
    async with ctx.db() as session:
        row = (
            await session.execute(
                select(TenantMember, AppUser)
                .join(AppUser, AppUser.id == TenantMember.user_id)
                .where(TenantMember.user_id == user_id)
            )
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return row[0], row[1]


@router.get("/membros/{user_id}/plantao", response_class=HTMLResponse)
async def duty_page(
    request: Request, user_id: uuid.UUID, ctx: ManagerCtx, error: str | None = None
) -> Response:
    member, user = await _duty_member(ctx, user_id)
    hours = BusinessHours.from_json(member.duty_hours) if member.duty_hours else None
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    rows = _hours_rows(hours or (hours_of(tenant) if tenant else BusinessHours.from_json(None)))
    return render(
        request, "panel/duty.html",
        {"person": user, "rows": rows, "follows_tenant": hours is None, "error": error},
        status_code=400 if error else 200,
    )  # fmt: skip


@router.post("/membros/{user_id}/plantao")
async def save_duty(request: Request, user_id: uuid.UUID, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    await _duty_member(ctx, user_id)
    duty: dict[str, Any] | None = None
    if form.get("follows_tenant") != "sim":
        try:
            duty = _parse_hours(form).to_json()
        except FormError as exc:
            return await duty_page(request, user_id, ctx, error=f"Não salvei: {exc}.")
    async with ctx.db() as session:
        await session.execute(
            update(TenantMember).where(TenantMember.user_id == user_id).values(duty_hours=duty)
        )
        await append_audit(
            session, tenant_id=ctx.tenant_id, event_type="duty_hours_changed",
            payload={"user_id": str(user_id), "by": str(ctx.user_id), "duty_hours": duty},
        )  # fmt: skip
    return redirect("/painel/configuracoes")
