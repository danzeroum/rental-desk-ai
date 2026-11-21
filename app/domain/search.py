"""Busca e ranking de casas (SPEC, seção 6). Puro, sem I/O.

Filtros obrigatórios: capacidade ≥ hóspedes; atributos exigidos = Sim (vazio não
conta); sem bloqueio sobreposto (só com dados frescos); cotável.
Orçamento: total ≤ orçamento; se nada passar, as até 3 mais próximas acima,
marcadas "acima do orçamento informado".
Ordem: sem flags antes de com flags; menor folga de capacidade; menor total; código.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.domain.catalog import BlockData, PropertyData, RateData
from app.domain.pricing import Quote, Stay, quote

MAX_OFFERS: Final = 3
FRESHNESS_LIMIT: Final = timedelta(hours=24)


class Attribute(StrEnum):
    """Atributos que o hóspede pode exigir (nomes do esquema de extração do SPEC)."""

    PISCINA = "piscina"
    PET = "pet"
    AR_QUARTOS = "ar_quartos"
    WIFI = "wifi"
    CHURRASQUEIRA = "churrasqueira"
    FESTA = "festa"


ATTRIBUTE_FIELDS: Final[dict[Attribute, str]] = {
    Attribute.PISCINA: "has_pool",
    Attribute.PET: "accepts_pets",
    Attribute.AR_QUARTOS: "ac_in_bedrooms",
    Attribute.WIFI: "has_wifi",
    Attribute.CHURRASQUEIRA: "has_barbecue",
    Attribute.FESTA: "allows_parties",
}


def attribute_value(prop: PropertyData, attribute: Attribute) -> bool | None:
    value: bool | None = getattr(prop, ATTRIBUTE_FIELDS[attribute])
    return value


@dataclass(frozen=True)
class SearchQuery:
    stay: Stay
    guests: int
    required: frozenset[Attribute] = field(default_factory=frozenset)
    budget_max_cents: int | None = None


@dataclass(frozen=True)
class Offer:
    property: PropertyData
    quote: Quote
    over_budget: bool
    availability_confirmed: bool
    """Falso com dados vencidos (RF13): o card diz "disponibilidade a confirmar"."""


@dataclass(frozen=True)
class SearchOutcome:
    offers: tuple[Offer, ...]
    data_stale: bool
    over_budget: bool


def is_data_stale(updated_at: datetime | None, now: datetime) -> bool:
    """Datas vencidas: nunca atualizadas ou atualizadas há mais de 24 h (RF13)."""
    return updated_at is None or now - updated_at > FRESHNESS_LIMIT


def _matches(prop: PropertyData, query: SearchQuery) -> bool:
    if prop.max_guests < query.guests:
        return False
    return all(attribute_value(prop, a) is True for a in query.required)


def _is_blocked(code: str, stay: Stay, blocks: Sequence[BlockData]) -> bool:
    return any(
        b.property_code == code and b.overlaps(stay.check_in, stay.check_out) for b in blocks
    )


def _rank_key(prop: PropertyData, q: Quote, guests: int) -> tuple[bool, int, int, str]:
    return (bool(q.flags), prop.max_guests - guests, q.total_cents, prop.code)


def search(
    query: SearchQuery,
    properties: Sequence[PropertyData],
    rates: Sequence[RateData],
    blocks: Sequence[BlockData],
    *,
    data_fresh: bool,
    limit: int = MAX_OFFERS,
) -> SearchOutcome:
    rates_by_code: dict[str, list[RateData]] = defaultdict(list)
    for rate in rates:
        rates_by_code[rate.property_code].append(rate)

    candidates: list[tuple[PropertyData, Quote]] = []
    for prop in properties:
        if not _matches(prop, query):
            continue
        if data_fresh and _is_blocked(prop.code, query.stay, blocks):
            continue
        q = quote(query.stay, rates_by_code.get(prop.code, []))
        if q is not None:
            candidates.append((prop, q))

    over_budget = False
    if query.budget_max_cents is not None:
        within = [(p, q) for p, q in candidates if q.total_cents <= query.budget_max_cents]
        if within or not candidates:
            candidates = within
        else:
            over_budget = True
            candidates = sorted(candidates, key=lambda c: (c[1].total_cents, c[0].code))[:limit]

    ranked = sorted(candidates, key=lambda c: _rank_key(c[0], c[1], query.guests))[:limit]
    offers = tuple(
        Offer(prop, q, over_budget=over_budget, availability_confirmed=data_fresh)
        for prop, q in ranked
    )
    return SearchOutcome(offers=offers, data_stale=not data_fresh, over_budget=over_budget)
