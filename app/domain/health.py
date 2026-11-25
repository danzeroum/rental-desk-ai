"""Saúde de uma imobiliária para o painel do operador (RF23). Puro, sem I/O."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.domain.meta_errors import short
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
    llm_failures_1h: int = 0
    failing_syncs: int = 0
    """Calendários iCal ou planilha Google com 3+ falhas seguidas (RF26, RF27)."""
    demo: bool = False
    """Imobiliária de demonstração: ficar dias sem mensagem é normal, não é alerta."""
    failed_deliveries_24h: int = 0
    """Mensagens nossas que a Meta aceitou mas não entregou (status `failed`)."""
    delivery_error_code: int | None = None


@dataclass(frozen=True)
class Health:
    level: Level
    reasons: list[str] = field(default_factory=list)


def _errors(data: HealthInput) -> list[str]:
    errors: list[str] = []
    if data.dead_jobs:
        errors.append(f"{data.dead_jobs} tarefa(s) na fila de falhas")
    if data.stuck_messages:
        errors.append(f"{data.stuck_messages} mensagem(ns) sem processar há mais de 10 min")
    if data.failed_deliveries_24h:
        code = f" ({short(data.delivery_error_code)})" if data.delivery_error_code else ""
        errors.append(
            f"{data.failed_deliveries_24h} mensagem(ns) não entregue(s) ao hóspede em 24 h{code}"
        )
    if (data.quality or "").upper() == "RED":
        errors.append("qualidade do número vermelha")
    if data.active and not data.has_approved_data:
        errors.append("sem planilha aprovada")
    return errors


def _alerts(data: HealthInput, now: datetime) -> list[str]:
    alerts: list[str] = []
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
    if data.failing_syncs:
        alerts.append(f"{data.failing_syncs} calendário(s)/planilha sem sincronizar (3+ falhas)")
    if data.llm_failures_1h:
        alerts.append(
            f"{data.llm_failures_1h} resposta(s) em contingência (IA fora) na última hora"
        )
    if data.validator_rejections_24h:
        alerts.append(
            f"{data.validator_rejections_24h} frase(s) recusada(s) pelo validador em 24 h"
        )
    quiet = data.last_inbound_at is None or now - data.last_inbound_at > QUIET_WEBHOOK
    if data.active and not data.demo and quiet:
        alerts.append("nenhuma mensagem recebida em 48 h")
    return alerts


def assess(data: HealthInput, now: datetime) -> Health:
    errors, alerts = _errors(data), _alerts(data, now)
    level = Level.ERROR if errors else Level.ALERT if alerts else Level.OK
    return Health(level, errors + alerts)


# --- Plataforma (alertas do operador, SPEC seção 10) -------------------------------------

QUEUE_ALERT: Final = 100
QUEUE_ERROR: Final = 500
DISK_ALERT: Final = 0.80
DISK_ERROR: Final = 0.90
BACKUP_MAX_AGE: Final = timedelta(hours=26)


@dataclass(frozen=True)
class PlatformInput:
    queue_backlog: int
    disk_used_ratio: float
    backup_at: datetime | None
    backup_remote: bool


@dataclass(frozen=True)
class Alert:
    key: str
    """Identifica o alerta para não repetir o aviso (ex.: `disco`, `tenant:<id>`)."""
    level: Level
    text: str


_NUMBERS: Final = re.compile(r"\d+")


def fingerprint(alert: Alert) -> str:
    """O que identifica o mesmo alerta para não repetir o aviso: chave, nível e texto **sem os
    números**. "3 mensagens não entregues" e depois "4 mensagens não entregues" são o mesmo
    problema; um e-mail por nova falha seria barulho (um a cada 5 min com o 130497). Mudar de
    nível (alerta → erro) ou de motivo avisa de novo."""
    text = _NUMBERS.sub("#", alert.text)
    return hashlib.sha256(f"{alert.key}|{alert.level}|{text}".encode()).hexdigest()[:16]


def assess_platform(data: PlatformInput, now: datetime) -> list[Alert]:
    alerts: list[Alert] = []
    if data.queue_backlog >= QUEUE_ALERT:
        level = Level.ERROR if data.queue_backlog >= QUEUE_ERROR else Level.ALERT
        alerts.append(Alert("fila", level, f"{data.queue_backlog} tarefas acumuladas na fila"))
    if data.disk_used_ratio >= DISK_ALERT:
        level = Level.ERROR if data.disk_used_ratio >= DISK_ERROR else Level.ALERT
        alerts.append(Alert("disco", level, f"disco {round(data.disk_used_ratio * 100)}% ocupado"))
    if data.backup_at is None or now - data.backup_at > BACKUP_MAX_AGE:
        alerts.append(Alert("backup", Level.ERROR, "nenhum backup concluído nas últimas 26 h"))
    elif not data.backup_remote:
        alerts.append(Alert("backup_externo", Level.ALERT, "último backup sem cópia externa (S3)"))
    return alerts
