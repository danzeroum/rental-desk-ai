"""Busca e ranking (SPEC, seção 6, "Busca e ranking")."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.catalog import BlockData, PropertyData, RateData
from app.domain.pricing import Flag, Stay
from app.domain.search import (
    Attribute,
    SearchOutcome,
    SearchQuery,
    is_data_stale,
    search,
)
from app.importer.sample import build_sample

R = 100


def house(
    code: str, guests: int, *, pool: bool | None = True, pets: bool | None = None
) -> PropertyData:
    return PropertyData(
        code=code, title=f"Casa {code}", neighborhood=None, photos_url=f"https://f/{code}",
        max_guests=guests, bedrooms=None, suites=None, bathrooms=None, parking_spots=None,
        has_pool=pool, accepts_pets=pets, allows_parties=None, ac_in_bedrooms=None,
        has_wifi=None, has_barbecue=None, beach_distance_m=None, public_notes=None,
    )  # fmt: skip


def rates_for(code: str, daily: int = 1000, *, package: int | None = 15000) -> list[RateData]:
    def r(start: date, end: date, value: int, pkg: int | None = None) -> RateData:
        return RateData(
            code, start, end, value * R, 1, "Réveillon" if pkg else None,
            pkg * R if pkg else None, pkg is not None, 300 * R,
        )  # fmt: skip

    return [
        r(date(2026, 12, 1), date(2026, 12, 28), daily),
        r(date(2026, 12, 28), date(2027, 1, 2), daily * 2, package),
        r(date(2027, 1, 2), date(2027, 2, 1), daily),
    ]


def run(
    properties: list[PropertyData],
    *,
    guests: int = 4,
    stay: tuple[date, date] = (date(2026, 12, 10), date(2026, 12, 13)),
    required: frozenset[Attribute] = frozenset(),
    budget: int | None = None,
    blocks: list[BlockData] | None = None,
    rates: list[RateData] | None = None,
    fresh: bool = True,
) -> SearchOutcome:
    all_rates = rates if rates is not None else [r for p in properties for r in rates_for(p.code)]
    query = SearchQuery(Stay(*stay), guests=guests, required=required, budget_max_cents=budget)
    return search(query, properties, all_rates, blocks or [], data_fresh=fresh)


def codes(outcome: SearchOutcome) -> list[str]:
    return [o.property.code for o in outcome.offers]


class TestMandatoryFilters:
    def test_capacity_at_least_guests_and_exact_is_ok(self) -> None:
        outcome = run([house("A", 11), house("B", 12), house("C", 14)], guests=12)
        assert codes(outcome) == ["B", "C"]

    def test_required_attribute_must_be_yes(self) -> None:
        props = [
            house("SIM", 6, pool=True),
            house("NAO", 6, pool=False),
            house("VAZIO", 6, pool=None),
        ]
        outcome = run(props, required=frozenset({Attribute.PISCINA}))
        assert codes(outcome) == ["SIM"]

    def test_several_required_attributes(self) -> None:
        props = [house("A", 6, pool=True, pets=True), house("B", 6, pool=True, pets=None)]
        outcome = run(props, required=frozenset({Attribute.PISCINA, Attribute.PET}))
        assert codes(outcome) == ["A"]

    def test_overlapping_block_excludes_when_data_fresh(self) -> None:
        stay = (date(2026, 12, 10), date(2026, 12, 13))
        blocks = [BlockData("A", date(2026, 12, 12), date(2026, 12, 15))]
        assert codes(run([house("A", 6), house("B", 6)], stay=stay, blocks=blocks)) == ["B"]

    @pytest.mark.parametrize(
        "block",
        [
            BlockData("A", date(2026, 12, 5), date(2026, 12, 10)),  # sai no dia do check-in
            BlockData("A", date(2026, 12, 13), date(2026, 12, 16)),  # entra no dia do check-out
        ],
    )
    def test_adjacent_block_does_not_conflict(self, block: BlockData) -> None:
        assert codes(run([house("A", 6)], blocks=[block])) == ["A"]

    def test_unquotable_house_is_excluded(self) -> None:
        props = [house("A", 6), house("B", 6)]
        rates = rates_for("A")  # B sem tarifas
        assert codes(run(props, rates=rates)) == ["A"]

    def test_block_of_other_house_does_not_matter(self) -> None:
        blocks = [BlockData("B", date(2026, 12, 1), date(2026, 12, 31))]
        assert codes(run([house("A", 6)], blocks=blocks)) == ["A"]


class TestStaleData:
    def test_blocks_ignored_and_availability_not_confirmed(self) -> None:
        blocks = [BlockData("A", date(2026, 12, 1), date(2026, 12, 31))]
        outcome = run([house("A", 6)], blocks=blocks, fresh=False)
        assert codes(outcome) == ["A"]
        assert outcome.data_stale
        assert all(not o.availability_confirmed for o in outcome.offers)

    def test_fresh_data_confirms_availability(self) -> None:
        outcome = run([house("A", 6)])
        assert not outcome.data_stale
        assert outcome.offers[0].availability_confirmed

    def test_24h_and_1min_is_stale(self) -> None:
        now = datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
        assert is_data_stale(now - timedelta(hours=24, minutes=1), now)

    def test_exactly_24h_is_still_fresh(self) -> None:
        now = datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
        assert not is_data_stale(now - timedelta(hours=24), now)

    def test_never_updated_is_stale(self) -> None:
        assert is_data_stale(None, datetime(2026, 12, 20, tzinfo=UTC))


class TestBudget:
    def test_filters_by_total(self) -> None:
        props = [house("BARATA", 6), house("CARA", 6)]
        rates = rates_for("BARATA", 500) + rates_for("CARA", 2000)
        # 3 noites: BARATA = 1500 + 300 limpeza; CARA = 6000 + 300
        outcome = run(props, rates=rates, budget=2000 * R)
        assert codes(outcome) == ["BARATA"]
        assert not outcome.over_budget
        assert not outcome.offers[0].over_budget

    def test_total_equal_to_budget_passes(self) -> None:
        outcome = run([house("A", 6)], rates=rates_for("A", 500), budget=1800 * R)
        assert codes(outcome) == ["A"]

    def test_nothing_within_budget_returns_nearest_above(self) -> None:
        props = [house(c, 6) for c in ("A", "B", "C", "D")]
        rates = [
            r for c, d in (("A", 900), ("B", 700), ("C", 800), ("D", 1000)) for r in rates_for(c, d)
        ]
        outcome = run(props, rates=rates, budget=1000 * R)
        assert outcome.over_budget
        assert codes(outcome) == ["B", "C", "A"]  # as 3 mais próximas acima
        assert all(o.over_budget for o in outcome.offers)


class TestRanking:
    def test_without_flags_before_flagged(self) -> None:
        props = [house("FLAG", 12), house("OK", 14)]
        rates = rates_for("FLAG") + rates_for("OK")
        # 30/12 a 02/01: pacote fechado parcial para FLAG; OK ganha pacote aberto
        rates = [
            r if r.property_code == "FLAG" else replace(r, package_closed=False) for r in rates
        ]
        outcome = run(props, guests=12, rates=rates, stay=(date(2026, 12, 30), date(2027, 1, 2)))
        assert codes(outcome) == ["OK", "FLAG"]
        assert Flag.REQUIRES_FULL_PACKAGE in outcome.offers[1].quote.flags

    def test_smaller_capacity_slack_first(self) -> None:
        outcome = run([house("GRANDE", 16), house("JUSTA", 12), house("MEDIA", 14)], guests=12)
        assert codes(outcome) == ["JUSTA", "MEDIA", "GRANDE"]

    def test_then_smaller_total_then_code(self) -> None:
        props = [house("B", 6), house("A", 6), house("C", 6)]
        rates = rates_for("B", 900) + rates_for("A", 900) + rates_for("C", 800)
        assert codes(run(props, rates=rates)) == ["C", "A", "B"]

    def test_at_most_three(self) -> None:
        props = [house(f"C{i}", 6) for i in range(6)]
        assert len(run(props).offers) == 3

    def test_order_is_stable_regardless_of_input_order(self) -> None:
        props = [house(f"C{i}", 6 + i % 3) for i in range(6)]
        assert codes(run(props)) == codes(run(list(reversed(props))))


class TestKickoffCases:
    """Casos do prompt de partida (docs/KICKOFF_PROMPT.md)."""

    def test_reveillon_for_12_with_pool_two_free_houses_with_closed_package(self) -> None:
        props = [
            house("LIVRE1", 12, pool=True),
            house("LIVRE2", 14, pool=True),
            house("OCUPADA", 12, pool=True),
            house("SEMPISCINA", 12, pool=False),
        ]
        blocks = [BlockData("OCUPADA", date(2026, 12, 28), date(2027, 1, 2))]
        outcome = run(
            props,
            guests=12,
            required=frozenset({Attribute.PISCINA}),
            stay=(date(2026, 12, 28), date(2027, 1, 2)),
            blocks=blocks,
        )
        assert codes(outcome) == ["LIVRE1", "LIVRE2"]
        for offer in outcome.offers:
            assert offer.quote.package is not None
            assert offer.quote.total_cents == (15000 + 300) * R
            assert offer.quote.flags == frozenset()

    def test_stay_30_12_to_02_01_in_closed_package_house_is_flagged(self) -> None:
        outcome = run([house("CASA", 12)], guests=10, stay=(date(2026, 12, 30), date(2027, 1, 2)))
        (offer,) = outcome.offers
        assert Flag.REQUIRES_FULL_PACKAGE in offer.quote.flags
        assert offer.quote.package is not None
        assert (offer.quote.package.start_date, offer.quote.package.end_date) == (
            date(2026, 12, 28),
            date(2027, 1, 2),
        )


def test_sample_workbook_reveillon_for_12_with_pool() -> None:
    """Validação com a planilha de exemplo (Demo Riviera): pacote inteiro, sem flags."""
    sample = build_sample(2026)
    query = SearchQuery(
        Stay(date(2026, 12, 28), date(2027, 1, 2)),
        guests=12,
        required=frozenset({Attribute.PISCINA}),
    )
    outcome = search(query, sample.properties, sample.rates, sample.blocks, data_fresh=True)
    # folga zero (12 lugares) primeiro, pelo menor total: 1700, 2200, 2400 de diária base
    assert codes(outcome) == ["CASA06", "CASA01", "CASA10"]
    assert [o.quote.total_cents for o in outcome.offers] == [
        (1700 * 2 * 5 + 300) * R,
        (2200 * 2 * 5 + 300) * R,
        (2400 * 2 * 5 + 300) * R,
    ]
    assert all(o.quote.package is not None and not o.quote.flags for o in outcome.offers)
