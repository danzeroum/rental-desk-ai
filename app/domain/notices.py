"""Avisos ativos por WhatsApp (templates aprovados na Meta) e textos ao hóspede da passagem.

Mensagem ativa a corretor e gestor só sai com consentimento registrado (SPEC, seção 9)
e por template aprovado, porque é enviada fora da janela de 24 h. O texto aprovado de
cada template está em `TEMPLATES` e em `docs/whatsapp-templates.md`; aqui o código só
monta os parâmetros {{1}}, {{2}}…
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from app.domain.render import br_date, brl


@dataclass(frozen=True)
class Template:
    name: str
    body: str
    """Texto submetido à Meta, com {{n}} nos parâmetros."""
    params: tuple[str, ...]
    """O que vai em cada parâmetro (documentação)."""


HANDOFF_TO_BROKER: Final = Template(
    name="passagem_corretor",
    body=(
        "Nova passagem da {{1}}: {{2}}. Hóspede: {{3}}. Pedido: {{4}}. "
        "Responda 1 para assumir a conversa."
    ),
    params=("nome da imobiliária", "motivo", "telefone do hóspede", "resumo do pedido"),
)
HANDOFF_COPY_TO_MANAGER: Final = Template(
    name="passagem_copia_gestor",
    body=(
        "Cópia para o gestor da {{1}}: passagem ({{2}}) do hóspede {{3}} enviada para {{4}}. "
        "Pedido: {{5}}."
    ),
    params=("nome da imobiliária", "motivo", "telefone do hóspede", "corretor avisado", "resumo"),
)
HANDOFF_ALERT: Final = Template(
    name="alerta_passagem",
    body=(
        "Atenção, {{1}}: a passagem ({{2}}) do hóspede {{3}} está há {{4}} minutos sem corretor. "
        "Assuma pelo painel ou peça a um corretor para responder 1 ao aviso."
    ),
    params=("nome da imobiliária", "motivo", "telefone do hóspede", "minutos comerciais"),
)
DAILY_REMINDER: Final = Template(
    name="lembrete_datas",
    body=(
        "Bom dia! Hora de conferir as datas das casas da {{1}} ({{2}}). Se está tudo certo, "
        "responda OK. Para bloquear, responda o código da casa com a entrada e a saída, por "
        "exemplo: CASA12 28/12-02/01. Painel: {{3}}"
    ),
    params=("nome da imobiliária", "bloqueios a partir de hoje", "link do painel"),
)
TEMPLATES: Final = (HANDOFF_TO_BROKER, HANDOFF_COPY_TO_MANAGER, HANDOFF_ALERT, DAILY_REMINDER)

REASON_LABELS: Final = {
    "choose": "escolheu uma casa",
    "negotiate": "quer negociar valor",
    "complaint": "reclamação",
    "payment": "pagamento",
    "cancel": "cancelamento",
    "human": "pediu um corretor",
    "out_of_scope": "assunto fora de hospedagem",
    "other_language": "mensagem em outro idioma",
    "clarification_attempts": "o assistente não entendeu o pedido",
    "llm_unavailable": "assistente fora do ar",
}

HANDOFF_OUT_OF_HOURS: Final = (
    "Nossos corretores voltam a atender {when}. Deixei anotado o que você pediu, e um corretor "
    "continua a conversa por aqui assim que o plantão começar."
)
GUEST_WAITING: Final = (
    "Nossos corretores estão com muitos atendimentos agora, mas um deles vai falar com você "
    "em breve, por aqui."
)
GUEST_WAITING_PHONE: Final = " Se preferir, ligue para {phone}."
BROKER_ASSUMED: Final = (
    "Você assumiu a conversa com o hóspede {guest}. O assistente fica em silêncio nessa conversa "
    "por 24 horas; responda pelo app WhatsApp Business."
)
BROKER_NOTHING_PENDING: Final = (
    "Não encontrei passagem aguardando você. Para assumir, responda 1 ao aviso de passagem."
)
BROKER_ALREADY_TAKEN: Final = "Essa passagem já foi assumida por outra pessoa."
MEMBER_HELP: Final = (
    "Comandos por aqui: 1 assume a passagem que avisamos; OK confirma as datas das casas; "
    "CASA12 28/12-02/01 bloqueia a casa (a noite da saída fica livre)."
)
REMINDER_CONFIRMED: Final = (
    "Obrigado! Datas confirmadas: o assistente volta a informar disponibilidade pelas "
    "próximas 24 horas."
)
REMINDER_BLOCKED: Final = "Bloqueado: {blocks}. A noite da saída fica livre."
REMINDER_ASK_OK: Final = " Se o resto está certo, responda OK."
REMINDER_PROBLEMS: Final = "Não consegui registrar: {problems}. Exemplo: CASA12 28/12-02/01."


def blocks_text(blocks: list[tuple[str, date, date]]) -> str:
    return "; ".join(f"{code} de {br_date(start)} a {br_date(end)}" for code, start, end in blocks)


def reminder_count_text(count: int) -> str:
    if count == 0:
        return "nenhum bloqueio a partir de hoje"
    return "1 bloqueio a partir de hoje" if count == 1 else f"{count} bloqueios a partir de hoje"


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def summary_text(summary: dict[str, Any]) -> str:
    """Resumo em uma linha (parâmetro de template não aceita quebra de linha)."""
    order = summary.get("pedido") or {}
    parts: list[str] = []
    if order.get("guests"):
        parts.append(f"{order['guests']} pessoas")
    if order.get("check_in") and order.get("check_out"):
        start, end = date.fromisoformat(order["check_in"]), date.fromisoformat(order["check_out"])
        parts.append(f"{br_date(start)} a {br_date(end)}")
    if order.get("required"):
        parts.append(", ".join(order["required"]))
    if order.get("budget_max_cents"):
        parts.append(f"até {brl(order['budget_max_cents'])}")
    if summary.get("casa_escolhida"):
        parts.append(f"escolheu {summary['casa_escolhida']}")
    elif summary.get("casas_oferecidas"):
        parts.append("ofertas: " + ", ".join(summary["casas_oferecidas"]))
    if summary.get("pendencias"):
        parts.append("a confirmar: " + "; ".join(summary["pendencias"]))
    return " · ".join(parts) or "sem detalhes ainda"


def guest_waiting_text(contact_phone: str | None) -> str:
    return GUEST_WAITING + (
        GUEST_WAITING_PHONE.format(phone=contact_phone) if contact_phone else ""
    )
