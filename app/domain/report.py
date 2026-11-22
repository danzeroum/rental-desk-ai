"""Relatório semanal da imobiliária (RF18). Puro, sem I/O e sem dados pessoais.

Período: de segunda 00h a segunda 00h (fim exclusivo), fuso America/Sao_Paulo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.domain.hours import BusinessHours
from app.domain.render import br_date, brl


@dataclass(frozen=True)
class ConversationFacts:
    contact_key: str
    """Identificador opaco do contato (id interno), só para contar distintos."""
    first_inbound_at: datetime
    first_response_at: datetime | None


@dataclass(frozen=True)
class HandoffFacts:
    created_at: datetime
    assumed_at: datetime | None
    outcome: str | None


@dataclass(frozen=True)
class WeeklyMetrics:
    period_start: date
    period_end: date
    contacts: int
    out_of_hours_pct: int | None
    first_response_median_s: int | None
    first_response_p95_s: int | None
    handoffs: int
    won: int
    lost: int
    pending: int
    without_outcome: int
    close_rate_pct: int | None
    assume_median_min: int | None
    offered_value_cents: int

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["period_start"], data["period_end"] = (
            self.period_start.isoformat(),
            self.period_end.isoformat(),
        )
        return data


def week_before(today: date) -> tuple[date, date]:
    """Semana completa anterior a `today` (segunda a segunda)."""
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7), this_monday


def _percentile(values: Sequence[float], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(math.ceil(pct / 100 * len(ordered)) - 1, 0)
    return round(ordered[index])


def _pct(part: int, total: int) -> int | None:
    return round(100 * part / total) if total else None


def compute(
    period: tuple[date, date],
    conversations: Sequence[ConversationFacts],
    handoffs: Sequence[HandoffFacts],
    offered_totals_cents: Sequence[int],
    hours: BusinessHours,
) -> WeeklyMetrics:
    responses = [
        (c.first_response_at - c.first_inbound_at).total_seconds()
        for c in conversations
        if c.first_response_at is not None
    ]
    outside = sum(not hours.is_open(c.first_inbound_at) for c in conversations)
    outcomes = [h.outcome for h in handoffs]
    won, lost = outcomes.count("won"), outcomes.count("lost")
    assumed = [(h.assumed_at - h.created_at).total_seconds() / 60 for h in handoffs if h.assumed_at]
    return WeeklyMetrics(
        period_start=period[0],
        period_end=period[1],
        contacts=len({c.contact_key for c in conversations}),
        out_of_hours_pct=_pct(outside, len(conversations)),
        first_response_median_s=_percentile(responses, 50),
        first_response_p95_s=_percentile(responses, 95),
        handoffs=len(handoffs),
        won=won,
        lost=lost,
        pending=outcomes.count("pending"),
        without_outcome=outcomes.count(None),
        close_rate_pct=_pct(won, won + lost),
        assume_median_min=_percentile(assumed, 50),
        offered_value_cents=sum(offered_totals_cents),
    )


def _or_dash(value: int | None, suffix: str) -> str:
    return "—" if value is None else f"{value}{suffix}"


def email_text(tenant_name: str, m: WeeklyMetrics, panel_url: str) -> tuple[str, str]:
    """Assunto e corpo do e-mail ao gestor."""
    last_day = m.period_end - timedelta(days=1)
    subject = f"Resumo da semana {br_date(m.period_start)} a {br_date(last_day)} — {tenant_name}"
    lines = [
        f"Resumo do assistente da {tenant_name}, "
        f"de {br_date(m.period_start)} a {br_date(last_day)}.",
        "",
        f"Contatos atendidos: {m.contacts}",
        f"Chegaram fora do horário dos corretores: {_or_dash(m.out_of_hours_pct, '%')}",
        f"Primeira resposta do assistente: mediana {_or_dash(m.first_response_median_s, ' s')}, "
        f"95% em até {_or_dash(m.first_response_p95_s, ' s')}",
        f"Passagens ao corretor: {m.handoffs}",
        f"Tempo até o corretor assumir (mediana): {_or_dash(m.assume_median_min, ' min')}",
        f"Desfechos: {m.won} fecharam, {m.lost} perderam, {m.pending} pendentes, "
        f"{m.without_outcome} sem desfecho marcado",
        f"Taxa de fechamento: {_or_dash(m.close_rate_pct, '%')}",
        f"Soma dos valores estimados das opções enviadas: {brl(m.offered_value_cents)}",
        "",
    ]
    if m.without_outcome:
        lines.append(f"Marque os desfechos que faltam (um clique cada): {panel_url}")
    else:
        lines.append(f"Painel: {panel_url}")
    return subject, "\n".join(lines) + "\n"


def whatsapp_params(tenant_name: str, m: WeeklyMetrics, panel_url: str) -> list[str]:
    """Parâmetros do template `resumo_semanal` (RF32): 5 linhas, sem dado pessoal."""
    last_day = m.period_end - timedelta(days=1)
    closing = f"{m.won} fecharam, {m.lost} perderam"
    if m.close_rate_pct is not None:
        closing += f" (taxa {m.close_rate_pct}%)"
    return [
        tenant_name,
        f"{br_date(m.period_start)[:5]} a {br_date(last_day)[:5]}",
        str(m.contacts),
        str(m.handoffs),
        closing,
        brl(m.offered_value_cents),
        panel_url,
    ]
