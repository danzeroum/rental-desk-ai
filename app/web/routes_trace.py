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

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.masking import mask_pii, mask_structured
from app.core.rls import global_session, tenant_session
from app.models import AuditLog, Contact, Conversation, Message, Tenant
from app.web.auth import PanelSession
from app.web.common import local_time, render, verify_csrf
from app.web.routes_operator import Operator

router = APIRouter(prefix="/operador/rastreio")

PERIODS: Final = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
MAX_ROWS: Final = 500
MAX_EXPORT: Final = 20_000

EVENT_LABELS: Final = {
    "message_received": "Mensagem recebida do hóspede",
    "response_sent": "Resposta enviada pelo assistente",
    "shadow_response": "Resposta registrada (modo sombra, não enviada)",
    "delivery_status": "Status de entrega (Meta)",
    "business_app_message": "Mensagem enviada pelo app WhatsApp Business",
    "broker_reply": "Resposta ao corretor",
    "rate_limit_notice": "Aviso de excesso de mensagens",
    "opt_out_confirmed": "Hóspede pediu PARAR",
    "handoff_notified": "Passagem avisada ao corretor",
    "handoff_alerted": "Passagem: alerta de demora",
    "handoff_guest_waiting": "Passagem: hóspede avisado da espera",
    "handoff_assumed": "Passagem assumida",
    "handoff_outcome": "Desfecho da passagem",
    "status_changed": "Situação da imobiliária alterada",
    "assistant_paused": "Assistente pausado/retomado",
    "settings_changed": "Configurações alteradas",
    "snapshot_imported": "Planilha enviada",
    "snapshot_approved": "Planilha aprovada",
    "snapshot_rejected": "Planilha recusada",
    "availability_confirmed": "Datas confirmadas",
    "block_added": "Bloqueio de datas incluído",
    "block_removed": "Bloqueio de datas removido",
    "support_access": "Acesso do suporte",
    "trace_viewed": "Rastreio consultado",
    "trace_exported": "Rastreio exportado",
    "subject_erased": "Dados do titular apagados (LGPD)",
    "tenant_created": "Imobiliária criada",
    "whatsapp_connected": "Número de WhatsApp conectado",
    "member_added": "Pessoa adicionada à equipe",
    "member_removed": "Pessoa removida da equipe",
    "consent_changed": "Aceite de avisos alterado",
    "duty_hours_changed": "Plantão do corretor alterado",
    "weekly_email_changed": "Relatório semanal por e-mail alterado",
    "weekly_report": "Relatório semanal enviado",
    "weekly_report_whatsapp": "Resumo semanal por WhatsApp",
    "reminder_sent": "Lembrete das 9h enviado",
    "reminder_reply": "Resposta ao lembrete das 9h",
    "calendar_feed_added": "Calendário iCal ligado",
    "calendar_feed_removed": "Calendário iCal removido",
    "calendar_feed_failing": "Calendário iCal com falhas",
    "sheet_linked": "Planilha Google ligada",
    "sheet_unlinked": "Planilha Google desligada",
    "sheet_blocks_synced": "Bloqueios sincronizados da planilha Google",
    "sheet_sync_failing": "Planilha Google com falhas",
    "simulator_used": "Simulador usado",
    "retention_applied": "Retenção aplicada (nome e telefone retirados)",
}
GROUPS: Final = {
    "conversas": ("message_received", "response_sent", "shadow_response", "delivery_status",
                  "business_app_message", "rate_limit_notice", "opt_out_confirmed"),
    "entregas": ("delivery_status",),
    "passagens": ("handoff_notified", "handoff_alerted", "handoff_guest_waiting",
                  "handoff_assumed", "handoff_outcome", "broker_reply"),
    "painel": ("status_changed", "assistant_paused", "settings_changed", "snapshot_imported",
               "snapshot_approved", "snapshot_rejected", "availability_confirmed", "block_added",
               "block_removed", "subject_erased", "retention_applied"),
    "acessos": ("support_access", "trace_viewed", "trace_exported"),
}  # fmt: skip
ACCESS_EVENTS: Final = GROUPS["acessos"]
"""Quem consultou o quê (LGPD). Fora da visão "todos" para não encobrir o atendimento; sempre
no CSV de "todos", que é a prova completa."""
DELIVERY_LABELS: Final = {
    "sent": "enviada", "delivered": "entregue", "read": "lida", "failed": "NÃO ENTREGUE",
}  # fmt: skip
_DIGITS: Final = re.compile(r"\D")


@dataclass(frozen=True)
class Filters:
    tenant_id: uuid.UUID | None
    period: str
    group: str
    phone: str
    conversation_id: uuid.UUID | None

    def as_audit(self) -> dict[str, Any]:
        """O que foi consultado, para a trilha: o telefone vai mascarado."""
        conversation = str(self.conversation_id) if self.conversation_id else None
        return {"period": self.period, "group": self.group or "todos",
                "phone": mask_phone(self.phone) if self.phone else None,
                "conversation_id": conversation}  # fmt: skip


@dataclass(frozen=True)
class Row:
    seq: int
    at: datetime
    event: str
    label: str
    conversation_id: uuid.UUID | None
    detail: str
    failed: bool


_FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    """Célula de CSV segura: texto que começa como fórmula (=, +, -, @) ganha um apóstrofo,
    para a planilha mostrar o texto em vez de executar (injeção de fórmula em CSV)."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_START):
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


def csv_line(cells: list[object]) -> str:
    return ",".join(csv_cell(cell) for cell in cells) + "\r\n"


def mask_phone(phone: str) -> str:
    """+5511996685998 → +55 11 9****-5998 (o suficiente para reconhecer, sem expor)."""
    digits = _DIGITS.sub("", phone)
    if len(digits) < 8:  # noqa: PLR2004
        return "****"
    return f"+{digits[:2]} {digits[2:4]} {digits[4]}****-{digits[-4:]}"


def _phone_variants(phone: str) -> list[str]:
    """O WhatsApp às vezes entrega celular do Brasil sem o 9 da frente: procura as duas formas."""
    digits = _DIGITS.sub("", phone)
    if len(digits) in (10, 11):
        digits = "55" + digits
    variants = {"+" + digits}
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":  # noqa: PLR2004
        variants.add("+" + digits[:4] + digits[5:])
    if digits.startswith("55") and len(digits) == 12:  # noqa: PLR2004
        variants.add("+" + digits[:4] + "9" + digits[4:])
    return sorted(variants)


def _delivery(payload: dict[str, Any]) -> str:
    label = DELIVERY_LABELS.get(str(payload.get("status")), str(payload.get("status")))
    if payload.get("error_code"):
        return f"{label}: código {payload['error_code']} ({payload.get('error') or 'sem motivo'})"
    return label


def _reply_text(payload: dict[str, Any]) -> str:
    return mask_pii(" ".join(str(payload.get("text") or "").split()))[:300]


_SUMMARIES: Final[dict[str, Callable[[dict[str, Any]], str]]] = {
    "delivery_status": _delivery,
    "response_sent": _reply_text,
    "shadow_response": _reply_text,
    "broker_reply": _reply_text,
    "message_received": lambda p: f"tipo: {p.get('kind', '?')}",
    "status_changed": lambda p: f"{p.get('from')} → {p.get('to')}",
    "handoff_outcome": lambda p: f"{p.get('outcome', '')} {p.get('reason') or ''}".strip(),
}


def summarize(event: str, payload: dict[str, Any]) -> str:
    """Resumo legível do evento. Textos passam pela máscara de dados pessoais."""
    handler = _SUMMARIES.get(event)
    if handler is not None:
        return handler(payload)
    keep = {k: v for k, v in payload.items() if k not in {"text", "external_id", "user_id"}}
    return json.dumps(mask_structured(keep), ensure_ascii=False, default=str)[:300] if keep else ""


TenantParam = Annotated[str | None, Query(alias="imobiliaria")]


def _filters(
    imobiliaria: TenantParam = None, periodo: str = "24h", tipo: str = "",
    conversa: str | None = None,
) -> Filters:  # fmt: skip
    """Filtros da URL: sem telefone (veja o cabeçalho do módulo)."""
    return build_filters(imobiliaria, periodo, tipo, "", conversa)


async def _posted_filters(request: Request) -> Filters:
    """Filtros de um formulário POST (CSRF conferido): o único caminho do telefone."""
    form = await verify_csrf(request)

    def field(name: str) -> str:
        return str(form.get(name, ""))

    return build_filters(
        field("imobiliaria") or None, field("periodo") or "24h", field("tipo"),
        field("telefone"), field("conversa") or None,
    )  # fmt: skip


def build_filters(
    imobiliaria: str | None, periodo: str, tipo: str, telefone: str, conversa: str | None
) -> Filters:
    def as_uuid(value: str | None) -> uuid.UUID | None:
        try:
            return uuid.UUID(value) if value else None
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND) from None

    return Filters(
        tenant_id=as_uuid(imobiliaria),
        period=periodo if periodo in PERIODS else "24h",
        group=tipo if tipo in GROUPS else "",
        phone=_DIGITS.sub("", telefone)[:15],
        conversation_id=as_uuid(conversa),
    )


async def _tenants(request: Request, operator: PanelSession) -> list[Tenant]:
    async with global_session(request.app.state.sessions, user_id=operator.user_uuid) as session:
        return list(await session.scalars(select(Tenant).order_by(Tenant.name)))


async def _conversations_of(session: AsyncSession, phone: str) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(Conversation.id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(Contact.phone_e164.in_(_phone_variants(phone)))
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


def _rows(events: list[AuditLog]) -> list[Row]:
    return [
        Row(
            seq=e.seq, at=e.created_at, event=e.event_type,
            label=EVENT_LABELS.get(e.event_type, e.event_type),
            conversation_id=e.conversation_id, detail=summarize(e.event_type, e.payload),
            failed=e.event_type == "delivery_status" and e.payload.get("status") == "failed",
        )
        for e in events
    ]  # fmt: skip


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
        "tenants": tenants, "filters": filters, "periods": list(PERIODS), "groups": list(GROUPS),
        "rows": [], "chain": None, "local": local_time, "max_rows": MAX_ROWS,
    }  # fmt: skip
    if filters.tenant_id is not None:
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
    now = request.app.state.auth.clock()
    async with tenant_session(
        request.app.state.sessions, filters.tenant_id, user_id=operator.user_uuid
    ) as session:
        events = await _events(session, filters, now, MAX_EXPORT, complete=True)
        await append_audit(
            session, tenant_id=filters.tenant_id, event_type="trace_exported",
            payload={"user_id": operator.user_id, "rows": len(events), **filters.as_audit()},
        )  # fmt: skip
    lines = [csv_line(["seq", "data_hora", "evento", "descricao", "conversa", "detalhes",
                       "hash", "hash_anterior"])]  # fmt: skip
    lines += [
        csv_line(
            [
                e.seq,
                e.created_at.isoformat(),
                e.event_type,
                EVENT_LABELS.get(e.event_type, e.event_type),
                e.conversation_id or "",
                json.dumps(mask_structured(e.payload), ensure_ascii=False, default=str),
                e.hash.hex(),
                e.prev_hash.hex(),
            ]
        )
        for e in reversed(events)
    ]
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
    imobiliaria: TenantParam = None,
) -> Response:  # fmt: skip
    """Mensagem por mensagem, com hora e status de entrega da Meta, mais os eventos da trilha."""
    filters = build_filters(imobiliaria, "30d", "", "", str(conversation_id))
    if filters.tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
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
            event_type="trace_viewed", payload={"user_id": operator.user_id, "view": "conversa"},
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
        if message.delivery_error_code:
            delivery += f" · código {message.delivery_error_code}: {message.delivery_error or ''}"
    return TimelineItem(message.created_at, who, body, delivery,
                        message.delivery_status == "failed")  # fmt: skip
