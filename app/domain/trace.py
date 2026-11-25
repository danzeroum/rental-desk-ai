"""Regras do rastreio (área do operador), sem I/O: rótulos, grupos, filtros, resumos e CSV.

As rotas em `app/web/routes_trace.py` só fazem HTTP e consulta ao banco; o que se mostra de cada
evento da trilha, e como, mora aqui e se testa sem banco (revisão de código C-1).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from app.core.masking import mask_pii, mask_structured
from app.domain.access import ACCESS_EVENTS, AccessReason, describe_payload, parse_reason
from app.domain.meta_errors import explain

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
    "subject_erasure_replayed": "Exclusões reaplicadas após restaurar backup",
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
               "block_removed", "subject_erased", "subject_erasure_replayed",
               "retention_applied"),
    "acessos": ACCESS_EVENTS,
}  # fmt: skip
GROUP_LABELS: Final = {
    "conversas": "Conversas (recebidas, respostas, entregas)",
    "entregas": "Status de entrega da Meta",
    "passagens": "Passagens ao corretor",
    "painel": "Ações no painel",
    "acessos": "Acessos (quem consultou e por quê)",
}
# ACCESS_EVENTS (quem consultou o quê, LGPD) fica fora da visão "todos" para não encobrir o
# atendimento; sempre no CSV de "todos", que é a prova completa.
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
    reason: AccessReason | None = None
    """Para quê o operador consulta (Risco-004): sem motivo, nada é listado."""

    def as_audit(self) -> dict[str, Any]:
        """O que foi consultado, e para quê, para a trilha: o telefone vai mascarado."""
        conversation = str(self.conversation_id) if self.conversation_id else None
        return {"period": self.period, "group": self.group or "todos",
                "phone": mask_phone(self.phone) if self.phone else None,
                "conversation_id": conversation,
                **(self.reason.as_audit() if self.reason else {})}  # fmt: skip


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


def phone_variants(phone: str) -> list[str]:
    """O WhatsApp às vezes entrega celular do Brasil sem o 9 da frente: procura as duas formas.
    Número sem o 55 ganha o DDI do Brasil, mas também é procurado como foi digitado (ex.: o
    número de teste da Meta, +1 555…, tem 11 dígitos como um celular brasileiro)."""
    typed = _DIGITS.sub("", phone)
    digits = "55" + typed if len(typed) in (10, 11) else typed
    variants = {"+" + digits, "+" + typed}
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":  # noqa: PLR2004
        variants.add("+" + digits[:4] + digits[5:])
    if digits.startswith("55") and len(digits) == 12:  # noqa: PLR2004
        variants.add("+" + digits[:4] + "9" + digits[4:])
    return sorted(variants)


def _delivery(payload: dict[str, Any]) -> str:
    label = DELIVERY_LABELS.get(str(payload.get("status")), str(payload.get("status")))
    if payload.get("error_code") or payload.get("error"):
        code = payload.get("error_code")
        return f"{label}: {explain(code if isinstance(code, int) else None, payload.get('error'))}"
    return label


def _reply_text(payload: dict[str, Any]) -> str:
    return mask_pii(" ".join(str(payload.get("text") or "").split()))[:300]


def _access(payload: dict[str, Any]) -> str:
    """Consulta ao rastreio: motivo e o que foi filtrado (telefone já gravado mascarado)."""
    parts = [describe_payload(payload) or "sem motivo registrado"]
    if payload.get("view") == "conversa":
        parts.append("linha do tempo de uma conversa")
    elif payload.get("phone"):
        parts.append(f"hóspede {payload['phone']}")
    return " · ".join(parts)


_SUMMARIES: Final[dict[str, Callable[[dict[str, Any]], str]]] = {
    "delivery_status": _delivery,
    "response_sent": _reply_text,
    "shadow_response": _reply_text,
    "broker_reply": _reply_text,
    "message_received": lambda p: f"tipo: {p.get('kind', '?')}",
    "status_changed": lambda p: f"{p.get('from')} → {p.get('to')}",
    "support_access": lambda p: describe_payload(p) or "sem motivo registrado",
    "trace_viewed": _access,
    "trace_exported": lambda p: _access(p) + f" · {p.get('rows', 0)} linha(s)",
    "handoff_outcome": lambda p: f"{p.get('outcome', '')} {p.get('reason') or ''}".strip(),
}


def summarize(event: str, payload: dict[str, Any]) -> str:
    """Resumo legível do evento. Textos passam pela máscara de dados pessoais."""
    handler = _SUMMARIES.get(event)
    if handler is not None:
        return handler(payload)
    keep = {k: v for k, v in payload.items() if k not in {"text", "external_id", "user_id"}}
    return json.dumps(mask_structured(keep), ensure_ascii=False, default=str)[:300] if keep else ""


@dataclass(frozen=True)
class TrailEvent:
    """Um evento da trilha como o CSV precisa (sem depender do modelo do banco)."""

    seq: int
    at: datetime
    event: str
    conversation_id: uuid.UUID | None
    payload: dict[str, Any]
    hash: bytes
    prev_hash: bytes


CSV_HEADER: Final[list[object]] = [
    "seq", "data_hora", "evento", "descricao", "conversa", "detalhes", "hash", "hash_anterior",
]  # fmt: skip


def export_cells(e: TrailEvent) -> list[object]:
    """Linha do CSV: o payload completo (mascarado) e os dois hashes, para conferir a cadeia
    fora do sistema."""
    return [
        e.seq, e.at.isoformat(), e.event, EVENT_LABELS.get(e.event, e.event),
        e.conversation_id or "",
        json.dumps(mask_structured(e.payload), ensure_ascii=False, default=str),
        e.hash.hex(), e.prev_hash.hex(),
    ]  # fmt: skip


def row_for(e: TrailEvent) -> Row:
    return Row(
        seq=e.seq, at=e.at, event=e.event, label=EVENT_LABELS.get(e.event, e.event),
        conversation_id=e.conversation_id, detail=summarize(e.event, e.payload),
        failed=e.event == "delivery_status" and e.payload.get("status") == "failed",
    )  # fmt: skip


def parse_filters(
    imobiliaria: str | None, periodo: str, tipo: str, telefone: str, conversa: str | None,
    *, motivo: str = "", detalhe: str = "",
) -> Filters:  # fmt: skip
    """Filtros validados. Período e grupo desconhecidos caem no padrão; id inválido levanta
    `ValueError`."""
    return Filters(
        tenant_id=uuid.UUID(imobiliaria) if imobiliaria else None,
        period=periodo if periodo in PERIODS else "24h",
        group=tipo if tipo in GROUPS else "",
        phone=_DIGITS.sub("", telefone)[:15],
        conversation_id=uuid.UUID(conversa) if conversa else None,
        reason=parse_reason(motivo, detalhe),
    )
