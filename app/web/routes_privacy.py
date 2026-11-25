"""Titular de dados (RF21) no painel e página pública do aviso de privacidade (RF02).

O telefone só trafega no corpo de formulários POST (nunca na URL, que vai para logs).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app.core.rls import global_session, tenant_session
from app.domain.access import ACCESS_ACTIONS, ACCESS_EVENTS, describe_payload
from app.domain.phones import InvalidPhoneError, to_e164
from app.models import AppUser, AuditLog, PrivacyNotice, Tenant, TenantRoute
from app.onboarding import SLUG_CHANNEL
from app.web.common import local_time, render, verify_csrf
from app.web.context import ManagerCtx
from app.workers import subject_rights

router = APIRouter()
CONFIRM_WORD: Final = "APAGAR"
CONFIRM_ERROR: Final = f"Digite {CONFIRM_WORD} para confirmar."


# --- Painel: direitos do titular ---------------------------------------------------------


@router.get("/painel/titular", response_class=HTMLResponse)
async def subject_page(request: Request, ctx: ManagerCtx) -> Response:
    return render(request, "panel/subject.html", {})


def _phone(form: dict[str, object]) -> str:
    try:
        return to_e164(str(form.get("phone", "")))
    except InvalidPhoneError as exc:
        raise ValueError(f"Telefone: {exc}.") from exc


@router.post("/painel/titular", response_class=HTMLResponse)
async def subject_action(request: Request, ctx: ManagerCtx) -> Response:
    form = await verify_csrf(request)
    action = str(form.get("action", "buscar"))
    try:
        phone = _phone(form)
    except ValueError as exc:
        return render(request, "panel/subject.html", {"error": str(exc)}, status_code=400)
    async with ctx.db() as session:
        if action == "exportar":
            data = await subject_rights.export(session, phone)
            if data is not None:
                return Response(
                    json.dumps(data, ensure_ascii=False, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="dados-do-titular.json"'},
                )
        elif action == "apagar":
            if str(form.get("confirm", "")).strip().upper() != CONFIRM_WORD:
                summary = await subject_rights.find(session, phone)
                return render(
                    request, "panel/subject.html",
                    {"phone": phone, "summary": summary, "error": CONFIRM_ERROR},
                    status_code=400,
                )  # fmt: skip
            erased = await subject_rights.erase(
                session, tenant_id=ctx.tenant_id, phone_e164=phone, user_id=ctx.user_id,
                ledger=request.app.state.erasure_ledger, now=request.app.state.auth.clock(),
            )  # fmt: skip
            if erased is not None:
                return render(request, "panel/subject.html", {"notice": "Dados apagados."})
        summary = await subject_rights.find(session, phone)
    if summary is None:
        return render(request, "panel/subject.html", {"phone": phone, "not_found": True})
    return render(request, "panel/subject.html", {"phone": phone, "summary": summary})


# --- Painel: acessos do suporte (revisão LGPD, Risco-005) -------------------------------

ACCESS_WINDOW: Final = timedelta(days=90)
MAX_ACCESSES: Final = 300


@dataclass(frozen=True)
class AccessRow:
    at: datetime
    who: str
    action: str
    reason: str


@router.get("/painel/acessos", response_class=HTMLResponse)
async def support_accesses(request: Request, ctx: ManagerCtx) -> Response:
    """O gestor (controlador) vê quando o fornecedor entrou na conta dele, o que viu e por quê."""
    since = request.app.state.auth.clock() - ACCESS_WINDOW
    async with ctx.db() as session:
        events = list(await session.scalars(
            select(AuditLog).where(
                AuditLog.event_type.in_(ACCESS_EVENTS), AuditLog.created_at >= since
            ).order_by(AuditLog.seq.desc()).limit(MAX_ACCESSES)
        ))  # fmt: skip
        ids = {_user_id(e.payload) for e in events} - {None}
        rows = await session.execute(
            select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(ids))
        )
        names = {row.id: row.display_name for row in rows}
    accesses = [
        AccessRow(
            e.created_at,
            names.get(_user_id(e.payload)) or "Suporte Rental Desk",
            ACCESS_ACTIONS[e.event_type],
            describe_payload(e.payload) or "não informado (antes desta versão)",
        )
        for e in events
    ]
    return render(request, "panel/accesses.html", {"rows": accesses, "local": local_time})


def _user_id(payload: dict[str, Any]) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(payload.get("user_id")))
    except ValueError:
        return None


# --- Aviso de privacidade público ---------------------------------------------------------


@router.get("/privacidade/{slug}", response_class=HTMLResponse)
@router.get("/privacidade/{slug}/v{version}", response_class=HTMLResponse)
async def privacy_notice(request: Request, slug: str, version: int | None = None) -> Response:
    sessions = request.app.state.sessions
    async with global_session(sessions) as session:
        tenant_id = await session.scalar(
            select(TenantRoute.tenant_id).where(
                TenantRoute.channel == SLUG_CHANNEL, TenantRoute.external_id == slug
            )
        )
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    async with tenant_session(sessions, tenant_id) as session:
        query = select(PrivacyNotice)
        query = (
            query.where(PrivacyNotice.version == version)
            if version
            else query.order_by(PrivacyNotice.version.desc())
        )
        notice = await session.scalar(query.limit(1))
        tenant = await session.get(Tenant, tenant_id)
    if notice is None or tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return render(
        request, "privacy.html",
        {"tenant_name": tenant.name, "notice": notice, "paragraphs": notice.content.split("\n\n")},
    )  # fmt: skip
