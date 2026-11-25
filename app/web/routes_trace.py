"""Rastreio (área do operador): a trilha de tudo o que aconteceu, para comprovar a um cliente.

Lê a trilha imutável (`audit_log`, encadeada por hash) de uma imobiliária, com filtros por
período, tipo de evento, telefone ou conversa, confere a cadeia de hash no banco e exporta em
CSV. A linha do tempo de uma conversa junta as mensagens (com o status de entrega da Meta) e
os eventos da trilha.

O telefone do hóspede só chega por formulário POST (com CSRF), nunca na URL: a URL vai para o
log de acesso do Caddy (revisão LGPD de 24/11/2026, Risco-002). Por GET, só filtros sem dado
pessoal (imobiliária, período, grupo, conversa).

Roda com o papel da aplicação (RLS): o operador lê cada imobiliária com `tenant_session`, como
qualquer requisição. Toda consulta e exportação fica registrada na própria trilha
(`trace_viewed`, `trace_exported`): quem viu dados de quem, e quando (LGPD).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.masking import mask_pii
from app.core.rls import global_session, tenant_session
from app.domain.access import ACCESS_EVENTS, REASONS
from app.domain.meta_errors import explain
from app.domain.trace import (
    CSV_HEADER,
    DELIVERY_LABELS,
    EVENT_LABELS,
    GROUP_LABELS,
    GROUPS,
    MAX_EXPORT,
    MAX_ROWS,
    PERIODS,
    Filters,
    Row,
    TrailEvent,
    csv_line,
    export_cells,
    mask_phone,
    parse_filters,
    phone_variants,
    row_for,
    summarize,
)
from app.models import AuditLog, Contact, Conversation, Message, Tenant
from app.web.auth import PanelSession
from app.web.common import local_time, render, verify_csrf
from app.web.routes_operator import Operator

router = APIRouter(prefix="/operador/rastreio")


TenantParam = Annotated[str | None, Query(alias="imobiliaria")]


def _filters(
    imobiliaria: TenantParam = None, periodo: str = "24h", tipo: str = "",
    conversa: str | None = None, motivo: str = "",
) -> Filters:  # fmt: skip
    """Filtros da URL: sem telefone e sem o detalhe do motivo (texto livre), que podem trazer
    dado pessoal; o código do motivo pode (veja o cabeçalho do módulo)."""
    return build_filters(imobiliaria, periodo, tipo, "", conversa, motivo=motivo)


async def _posted_filters(request: Request) -> Filters:
    """Filtros de um formulário POST (CSRF conferido): o único caminho do telefone."""
    form = await verify_csrf(request)

    def field(name: str) -> str:
        return str(form.get(name, ""))

    return build_filters(
        field("imobiliaria") or None, field("periodo") or "24h", field("tipo"),
        field("telefone"), field("conversa") or None,
        motivo=field("motivo"), detalhe=field("detalhe"),
    )  # fmt: skip


def build_filters(
    imobiliaria: str | None, periodo: str, tipo: str, telefone: str, conversa: str | None,
    *, motivo: str = "", detalhe: str = "",
) -> Filters:  # fmt: skip
    """Filtros já validados; id de imobiliária ou conversa inválido vira 404 (não diz se existe)."""
    try:
        return parse_filters(
            imobiliaria, periodo, tipo, telefone, conversa, motivo=motivo, detalhe=detalhe
        )
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from None


async def _tenants(request: Request, operator: PanelSession) -> list[Tenant]:
    async with global_session(request.app.state.sessions, user_id=operator.user_uuid) as session:
        return list(await session.scalars(select(Tenant).order_by(Tenant.name)))


async def _conversations_of(session: AsyncSession, phone: str) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(Conversation.id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(Contact.phone_e164.in_(phone_variants(phone)))
    )
    return list(rows)


async def _events(
    session: AsyncSession, filters: Filters, now: datetime, limit: int, *, complete: bool = False
) -> list[AuditLog]:
    """Eventos do filtro. Sem grupo, a tela omite os acessos; `complete` (CSV) traz todos."""
    query = select(AuditLog).where(AuditLog.created_at >= now - PERIODS[filters.period])
    if filters.group:
        query = query.where(AuditLog.event_type.in_(GROUPS[filters.group]))
    elif not complete:
        query = query.where(AuditLog.event_type.not_in(ACCESS_EVENTS))
    if filters.conversation_id:
        query = query.where(AuditLog.conversation_id == filters.conversation_id)
    elif filters.phone:
        query = query.where(AuditLog.conversation_id.in_(
            await _conversations_of(session, filters.phone) or [uuid.uuid4()]
        ))  # fmt: skip
    return list(await session.scalars(query.order_by(AuditLog.seq.desc()).limit(limit)))


async def _chain(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[int, int | None]:
    """(eventos na trilha, primeiro `seq` quebrado ou None se íntegra) — conferido no banco."""
    total = await session.scalar(select(text("count(*)")).select_from(AuditLog))
    broken = await session.scalar(text("SELECT audit_log_verify(:t)"), {"t": tenant_id})
    return int(total or 0), broken


def _trail(e: AuditLog) -> TrailEvent:
    return TrailEvent(
        e.seq, e.created_at, e.event_type, e.conversation_id, e.payload, e.hash, e.prev_hash
    )


def _rows(events: list[AuditLog]) -> list[Row]:
    return [row_for(_trail(e)) for e in events]


FiltersDep = Annotated[Filters, Depends(_filters)]
PostedFiltersDep = Annotated[Filters, Depends(_posted_filters)]


@router.get("", response_class=HTMLResponse)
async def trace_page(request: Request, operator: Operator, filters: FiltersDep) -> Response:
    return await _page(request, operator, filters)


@router.post("", response_class=HTMLResponse)
async def trace_search(request: Request, operator: Operator, filters: PostedFiltersDep) -> Response:
    """Mesma página, com filtro por telefone vindo do corpo do formulário."""
    return await _page(request, operator, filters)


async def _page(request: Request, operator: PanelSession, filters: Filters) -> Response:
    tenants = await _tenants(request, operator)
    context: dict[str, Any] = {
        "tenants": tenants, "filters": filters, "periods": list(PERIODS), "groups": GROUPS,
        "group_labels": GROUP_LABELS, "reasons": REASONS, "rows": [], "chain": None,
        "local": local_time, "max_rows": MAX_ROWS,
        "missing_reason": filters.tenant_id is not None and filters.reason is None,
    }  # fmt: skip
    if filters.tenant_id is not None and filters.reason is not None:
        now = request.app.state.auth.clock()
        async with tenant_session(
            request.app.state.sessions, filters.tenant_id, user_id=operator.user_uuid
        ) as session:
            events = await _events(session, filters, now, MAX_ROWS)
            context["chain"] = await _chain(session, filters.tenant_id)
            await append_audit(
                session, tenant_id=filters.tenant_id, event_type="trace_viewed",
                payload={"user_id": operator.user_id, **filters.as_audit()},
            )  # fmt: skip
        context["rows"] = _rows(events)
    return render(request, "operator/trace.html", context)


@router.post("/exportar")
async def trace_export(request: Request, operator: Operator, filters: PostedFiltersDep) -> Response:
    """CSV com os eventos completos e o hash de cada um: prova verificável fora do sistema."""
    if filters.tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if filters.reason is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escolha o motivo da consulta.")
    now = request.app.state.auth.clock()
    async with tenant_session(
        request.app.state.sessions, filters.tenant_id, user_id=operator.user_uuid
    ) as session:
        events = await _events(session, filters, now, MAX_EXPORT, complete=True)
        await append_audit(
            session, tenant_id=filters.tenant_id, event_type="trace_exported",
            payload={"user_id": operator.user_id, "rows": len(events), **filters.as_audit()},
        )  # fmt: skip
    lines = [csv_line(CSV_HEADER)]
    lines += [csv_line(export_cells(_trail(e))) for e in reversed(events)]
    name = f"rastreio-{filters.tenant_id}-{now:%Y%m%d-%H%M}.csv"
    return Response(
        "".join(lines),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@dataclass(frozen=True)
class TimelineItem:
    at: datetime
    who: str
    text: str
    delivery: str
    failed: bool


@router.get("/conversa/{conversation_id}", response_class=HTMLResponse)
async def conversation_timeline(
    request: Request, conversation_id: uuid.UUID, operator: Operator,
    imobiliaria: TenantParam = None, motivo: str = "",
) -> Response:  # fmt: skip
    """Mensagem por mensagem, com hora e status de entrega da Meta, mais os eventos da trilha.
    Vem de um resultado do rastreio, que já tem o motivo; sem ele, volta para o rastreio."""
    filters = build_filters(imobiliaria, "30d", "", "", str(conversation_id), motivo=motivo)
    if filters.tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if filters.reason is None:
        return RedirectResponse(
            f"/operador/rastreio?imobiliaria={filters.tenant_id}", status.HTTP_303_SEE_OTHER
        )
    async with tenant_session(
        request.app.state.sessions, filters.tenant_id, user_id=operator.user_uuid
    ) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        contact = await session.get(Contact, conversation.contact_id)
        messages = list(await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        ))  # fmt: skip
        events = list(await session.scalars(
            select(AuditLog).where(AuditLog.conversation_id == conversation_id)
            .order_by(AuditLog.seq)
        ))  # fmt: skip
        await append_audit(
            session, tenant_id=filters.tenant_id, conversation_id=conversation_id,
            event_type="trace_viewed",
            payload={"user_id": operator.user_id, "view": "conversa", **filters.as_audit()},
        )  # fmt: skip
    items = [_message_item(m) for m in messages]
    items += [
        TimelineItem(e.created_at, "trilha", f"{EVENT_LABELS.get(e.event_type, e.event_type)}"
                     f" · {summarize(e.event_type, e.payload)}".rstrip(" ·"), "", False)
        for e in events
        if e.event_type not in {"message_received", "response_sent", "delivery_status"}
    ]  # fmt: skip
    items.sort(key=lambda item: item.at)
    phone = mask_phone(contact.phone_e164) if contact and contact.phone_e164 else "(anonimizado)"
    return render(
        request, "operator/trace_conversation.html",
        {"items": items, "conversation": conversation, "phone": phone, "filters": filters,
         "local": local_time},
    )  # fmt: skip


def _message_item(message: Message) -> TimelineItem:
    who = {"in": "hóspede"}.get(message.direction, "assistente")
    if message.origin == "business_app":
        who = "app WhatsApp Business"
    body = mask_pii(message.body or message.transcript or f"[{message.kind}]")
    delivery = ""
    if message.direction == "out":
        status_label = DELIVERY_LABELS.get(message.delivery_status or "", "sem confirmação")
        delivery = status_label
        if message.delivery_status_at:
            delivery += f" em {local_time(message.delivery_status_at, '%d/%m %H:%M:%S')}"
        if message.delivery_error_code or message.delivery_error:
            delivery += f" · {explain(message.delivery_error_code, message.delivery_error)}"
    return TimelineItem(message.created_at, who, body, delivery,
                        message.delivery_status == "failed")  # fmt: skip
