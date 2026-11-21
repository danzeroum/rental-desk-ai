"""Valor estimado de uma estadia (SPEC, seção 6). Puro, sem I/O.

Regras:
1. Cada noite precisa de uma tarifa cujo período a cubra; senão a casa não é cotável.
2. Pacote tocado pela estadia:
   - coberto inteiro → valor do pacote no lugar das diárias daquelas noites;
   - coberto em parte e fechado → flag `requires_full_package`; a estimativa usa o
     pacote completo (é o que a casa aluga nessas datas) (D021);
   - coberto em parte e aberto → diárias normais do período.
3. Menos noites que o maior mínimo dos períodos tocados → flag `below_min_nights`.
4. Taxa de limpeza: uma vez, a da tarifa da noite do check-in.
5. Tudo em centavos (inteiros).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from app.domain.catalog import RateData


class InvalidStayError(ValueError):
    """Estadia sem noites (check-out igual ou anterior ao check-in)."""


class Flag(StrEnum):
    REQUIRES_FULL_PACKAGE = "requires_full_package"
    BELOW_MIN_NIGHTS = "below_min_nights"


@dataclass(frozen=True)
class Stay:
    check_in: date
    check_out: date

    def __post_init__(self) -> None:
        if self.check_out <= self.check_in:
            raise InvalidStayError("o check-out precisa ser depois do check-in")

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def each_night(self) -> Iterator[date]:
        for offset in range(self.nights):
            yield self.check_in + timedelta(days=offset)

    def covers_period(self, start: date, end: date) -> bool:
        return self.check_in <= start and end <= self.check_out


@dataclass(frozen=True)
class PackageInfo:
    name: str
    start_date: date
    end_date: date
    total_cents: int


@dataclass(frozen=True)
class Quote:
    property_code: str
    nights: int
    total_cents: int
    cleaning_fee_cents: int
    flags: frozenset[Flag] = field(default_factory=frozenset)
    min_nights_required: int | None = None
    package: PackageInfo | None = None


def _rate_for(night: date, rates: Sequence[RateData]) -> RateData | None:
    return next((r for r in rates if r.covers(night)), None)


def _package_info(rate: RateData) -> PackageInfo:
    if rate.package_name is None or rate.package_cents is None:
        raise ValueError(f"tarifa de {rate.property_code} sem pacote completo")
    return PackageInfo(rate.package_name, rate.start_date, rate.end_date, rate.package_cents)


def quote(stay: Stay, rates: Sequence[RateData]) -> Quote | None:
    """Cota a estadia com as tarifas **de uma casa**. `None` = casa não cotável."""
    nightly: list[tuple[date, RateData]] = []
    for night in stay.each_night():
        rate = _rate_for(night, rates)
        if rate is None:
            return None
        nightly.append((night, rate))

    touched = list(dict.fromkeys(rate for _, rate in nightly))
    flags: set[Flag] = set()
    priced_as_package: set[RateData] = set()
    package: PackageInfo | None = None
    total = 0
    for rate in touched:
        if not rate.is_package:
            continue
        if stay.covers_period(rate.start_date, rate.end_date) or rate.package_closed:
            if not stay.covers_period(rate.start_date, rate.end_date):
                flags.add(Flag.REQUIRES_FULL_PACKAGE)
            priced_as_package.add(rate)
            package = _package_info(rate)
            total += package.total_cents

    total += sum(rate.daily_cents for _, rate in nightly if rate not in priced_as_package)

    required = max(rate.min_nights for rate in touched)
    if stay.nights < required:
        flags.add(Flag.BELOW_MIN_NIGHTS)

    cleaning = nightly[0][1].cleaning_fee_cents
    return Quote(
        property_code=touched[0].property_code,
        nights=stay.nights,
        total_cents=total + cleaning,
        cleaning_fee_cents=cleaning,
        flags=frozenset(flags),
        min_nights_required=required if Flag.BELOW_MIN_NIGHTS in flags else None,
        package=package,
    )
