"""Catálogo aprovado da imobiliária: imóveis, tarifas e bloqueios. Puro, sem I/O.

Períodos seguem a convenção da estadia: `start_date` ≤ noite < `end_date`.
Dinheiro em centavos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PropertyData:
    code: str
    title: str
    neighborhood: str | None
    photos_url: str | None
    max_guests: int
    bedrooms: int | None
    suites: int | None
    bathrooms: int | None
    parking_spots: int | None
    has_pool: bool | None
    accepts_pets: bool | None
    allows_parties: bool | None
    ac_in_bedrooms: bool | None
    has_wifi: bool | None
    has_barbecue: bool | None
    beach_distance_m: int | None
    public_notes: str | None


@dataclass(frozen=True)
class RateData:
    """Período de tarifa; `end_date` exclusivo (noites de `start_date` até a véspera)."""

    property_code: str
    start_date: date
    end_date: date
    daily_cents: int
    min_nights: int
    package_name: str | None
    package_cents: int | None
    package_closed: bool
    cleaning_fee_cents: int

    def covers(self, night: date) -> bool:
        return self.start_date <= night < self.end_date

    @property
    def is_package(self) -> bool:
        return self.package_cents is not None


@dataclass(frozen=True)
class BlockData:
    property_code: str
    start_date: date
    end_date: date

    def overlaps(self, start: date, end: date) -> bool:
        return self.start_date < end and start < self.end_date
