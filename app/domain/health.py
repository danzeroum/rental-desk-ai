"""Saúde de uma imobiliária para o painel do operador (RF23). Puro, sem I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.domain.search import FRESHNESS_LIMIT

QUIET_WEBHOOK: Final = timedelta(hours=48)
HANDOFF_WAIT_ALERT: Final = timedelta(minutes=15)
STUCK_AFTER: Final = timedelta(minutes=10)


class Level(StrEnum):
    OK = "ok"
    ALERT = "alerta"
    ERROR = "erro"


@dataclass(frozen=True)
class HealthInput:
    active: bool
    paused: bool
    last_inbound_at: datetime | None
    availability_updated_at: datetime | None
    open_handoffs: int
    oldest_open_handoff_at: datetime | None
    validator_rejections_24h: int
    stuck_messages: int
    dead_jobs: int
    quality: str | None
    has_approved_data: bool


@dataclass(frozen=True)
class Health:
    level: Level
    reasons: list[str] = field(default_factory=list)


def assess(data: HealthInput, now: datetime) -> Health:
    errors: list[str] = []
    alerts: list[str] = []
    if data.dead_jobs:
        errors.append(f"{data.dead_jobs} tarefa(s) na fila de falhas")
    if data.stuck_messages:
        errors.append(f"{data.stuck_messages} mensagem(ns) sem processar há mais de 10 min")
    if (data.quality or "").upper() == "RED":
        errors.append("qualidade do número vermelha")
    if data.active and not data.has_approved_data:
        errors.append("sem planilha aprovada")
    if data.paused:
        alerts.append("assistente pausado")
    if (data.quality or "").upper() == "YELLOW":
        alerts.append("qualidade do número amarela")
    updated = data.availability_updated_at
    if data.active and (updated is None or now - updated > FRESHNESS_LIMIT):
        alerts.append("datas vencidas (mais de 24 h)")
    if data.oldest_open_handoff_at and now - data.oldest_open_handoff_at > HANDOFF_WAIT_ALERT:
        alerts.append(
            f"{data.open_handoffs} passagem(ns) aguardando, a mais antiga há mais de 15 min"
        )
    if data.validator_rejections_24h:
        alerts.append(
            f"{data.validator_rejections_24h} frase(s) recusada(s) pelo validador em 24 h"
        )
    if data.active and (data.last_inbound_at is None or now - data.last_inbound_at > QUIET_WEBHOOK):
        alerts.append("nenhuma mensagem recebida em 48 h")
    level = Level.ERROR if errors else Level.ALERT if alerts else Level.OK
    return Health(level, errors + alerts)
