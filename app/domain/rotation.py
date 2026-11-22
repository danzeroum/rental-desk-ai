"""Rodízio de corretores de plantão (RF29). Puro, sem I/O.

- Nova passagem: vai para quem recebeu passagem há mais tempo (ou nunca recebeu);
  empate pela ordem do cadastro. Distribui a carga de forma estável.
- Sem assumir em 10 min comerciais: vai para o próximo da lista, em ciclo.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Broker:
    user_id: uuid.UUID
    name: str
    phone_e164: str


def pick_for_new(
    brokers: Sequence[Broker], last_assigned: Mapping[uuid.UUID, datetime | None]
) -> Broker | None:
    if not brokers:
        return None
    order = {b.user_id: index for index, b in enumerate(brokers)}

    def key(broker: Broker) -> tuple[bool, float, int]:
        last = last_assigned.get(broker.user_id)
        return (last is not None, last.timestamp() if last else 0.0, order[broker.user_id])

    return min(brokers, key=key)


def next_in_cycle(brokers: Sequence[Broker], current: uuid.UUID | None) -> Broker | None:
    if not brokers:
        return None
    ids = [b.user_id for b in brokers]
    if current not in ids:
        return brokers[0]
    return brokers[(ids.index(current) + 1) % len(brokers)]
