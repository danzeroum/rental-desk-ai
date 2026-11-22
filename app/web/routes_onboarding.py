"""Assistente de implantação (RF28) e simulador de conversa no painel."""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.whatsapp import Channel, GatewayError, WhatsAppGateway
from app.core.audit import append_audit
from app.domain.onboarding import OnboardingFacts, current, steps
from app.domain.phones import InvalidPhoneError, to_e164
from app.models import AuditLog, DataSnapshot, SheetLink, Tenant, TenantRoute, WhatsAppAccount
from app.web.common import redirect, render, verify_csrf
from app.web.context import ManagerCtx, TenantContext
from app.workers.channel import seal_token
from app.workers.simulator import Simulator

router = APIRouter(prefix="/painel")
WHATSAPP_CHANNEL: Final = "whatsapp"


async def onboarding_facts(session: AsyncSession, tenant: Tenant) -> OnboardingFacts:
    async def count(query: Any) -> int:
        return int(await session.scalar(query) or 0)

    shadow_since = await session.scalar(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.event_type == "status_changed", AuditLog.payload["to"].astext == "shadow"
        )
    )
    return OnboardingFacts(
        has_number=await count(select(func.count()).select_from(WhatsAppAccount)) > 0,
        has_saved_version=await count(select(func.count()).select_from(DataSnapshot)) > 0,
        has_sheet_link=await count(select(func.count()).select_from(SheetLink)) > 0,
        has_approved=tenant.active_snapshot_id is not None,
        simulator_turns=await count(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "simulator_used")
        ),
        status=tenant.status,
        shadow_since=shadow_since,
    )


async def _tenant(ctx: TenantContext, session: AsyncSession) -> Tenant:
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return tenant


@router.get("/implantacao", response_class=HTMLResponse)
async def onboarding_page(request: Request, ctx: ManagerCtx, error: str | None = None) -> Response:
    async with ctx.db() as session:
        tenant = await _tenant(ctx, session)
        facts = await onboarding_facts(session, tenant)
        account = await session.scalar(select(WhatsAppAccount).limit(1))
    all_steps = steps(facts, ctx.now())
    return render(
        request, "panel/onboarding.html",
        {"steps": all_steps, "current": current(all_steps), "account": account, "error": error,
         "done": sum(s.done for s in all_steps)},
        status_code=400 if error else 200,
    )  # fmt: skip


@router.post("/whatsapp")
async def connect_whatsapp(request: Request, ctx: ManagerCtx) -> Response:
    """Passo 1: dados do número vindos do cadastro no parceiro (coexistência); testa antes."""
    form = await verify_csrf(request)
    pnid = "".join(ch for ch in str(form.get("phone_number_id", "")) if ch.isdigit())
    token = str(form.get("access_token", "")).strip()
    try:
        display = to_e164(str(form.get("display_phone", "")))
    except InvalidPhoneError as exc:
        return await onboarding_page(request, ctx, error=f"Telefone do número: {exc}.")
    if not pnid or not token:
        return await onboarding_page(
            request, ctx, error="Informe o ID do número e o token do parceiro."
        )
    gateway: WhatsAppGateway = request.app.state.gateway
    try:
        quality = await gateway.phone_quality(Channel(phone_number_id=pnid, access_token=token))
    except GatewayError as exc:
        detail = exc.status or "de rede"
        message = f"O parceiro recusou a conexão (erro {detail}). Confira o ID e o token."
        return await onboarding_page(request, ctx, error=message)
    keyring = request.app.state.keyring
    try:
        async with ctx.db() as session:
            account = await session.scalar(select(WhatsAppAccount).limit(1).with_for_update())
            if account is None:
                session.add(
                    WhatsAppAccount(
                        tenant_id=ctx.tenant_id, phone_number_id=pnid, display_phone=display,
                        access_token_enc=seal_token(keyring, ctx.tenant_id, token),
                        quality_rating=quality,
                    )
                )  # fmt: skip
                session.add(
                    TenantRoute(channel=WHATSAPP_CHANNEL, external_id=pnid, tenant_id=ctx.tenant_id)
                )
            else:
                account.access_token_enc = seal_token(keyring, ctx.tenant_id, token)
                account.quality_rating = quality
            await append_audit(
                session, tenant_id=ctx.tenant_id, event_type="whatsapp_connected",
                payload={"user_id": str(ctx.user_id), "phone_number_id": pnid},
            )  # fmt: skip
    except IntegrityError:
        return await onboarding_page(
            request, ctx, error="Esse número já está ligado a outra imobiliária."
        )
    return redirect("/painel/implantacao")


# --- Simulador ---------------------------------------------------------------------------


def simulator(request: Request) -> Simulator:
    service: Simulator = request.app.state.simulator
    return service


@router.get("/simulador", response_class=HTMLResponse)
async def simulator_page(request: Request, ctx: ManagerCtx) -> Response:
    exchanges = await simulator(request).transcript(ctx.tenant_id, ctx.user_id)
    return render(request, "panel/simulator.html", {"exchanges": exchanges})


@router.post("/simulador")
async def simulator_say(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    if form.get("action") == "recomecar":
        await simulator(request).reset(ctx.tenant_id, ctx.user_id)
    elif text := str(form.get("text", "")).strip():
        await simulator(request).say(ctx.tenant_id, ctx.user_id, text)
    return redirect("/painel/simulador")
