"""Cálculo do valor estimado (SPEC, seção 6, "Valor estimado")."""

from datetime import date

import pytest

from app.domain.catalog import RateData
from app.domain.pricing import Flag, InvalidStayError, Stay, quote

R = 100  # centavos por real


def rate(
    start: date,
    end: date,
    daily: int,
    *,
    min_nights: int = 1,
    cleaning: int = 300,
    package: int | None = None,
    closed: bool = False,
    code: str = "CASA01",
) -> RateData:
    return RateData(
        property_code=code,
        start_date=start,
        end_date=end,
        daily_cents=daily * R,
        min_nights=min_nights,
        package_name="Réveillon" if package is not None else None,
        package_cents=package * R if package is not None else None,
        package_closed=closed,
        cleaning_fee_cents=cleaning * R,
    )


LOW = rate(date(2026, 12, 1), date(2026, 12, 28), 1000, min_nights=2, cleaning=300)
JAN = rate(date(2027, 1, 2), date(2027, 2, 1), 1500, min_nights=3, cleaning=350)


def season(*, closed: bool) -> list[RateData]:
    reveillon = rate(
        date(2026, 12, 28), date(2027, 1, 2), 2000,
        min_nights=5, cleaning=400, package=15000, closed=closed,
    )  # fmt: skip
    return [LOW, reveillon, JAN]


def stay(check_in: tuple[int, int, int], check_out: tuple[int, int, int]) -> Stay:
    return Stay(date(*check_in), date(*check_out))


class TestStay:
    def test_nights_exclude_check_out(self) -> None:
        s = stay((2026, 12, 10), (2026, 12, 13))
        assert s.nights == 3
        assert list(s.each_night()) == [date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 12)]

    @pytest.mark.parametrize("out", [(2026, 12, 10), (2026, 12, 9)])
    def test_check_out_must_be_after_check_in(self, out: tuple[int, int, int]) -> None:
        with pytest.raises(InvalidStayError):
            stay((2026, 12, 10), out)


class TestDailyRates:
    def test_simple_stay(self) -> None:
        q = quote(stay((2026, 12, 10), (2026, 12, 13)), [LOW])
        assert q is not None
        assert (q.total_cents, q.nights, q.flags) == (3 * 1000 * R + 300 * R, 3, frozenset())
        assert isinstance(q.total_cents, int)

    def test_stay_crossing_two_rates(self) -> None:
        high = rate(date(2026, 12, 15), date(2026, 12, 28), 1800, cleaning=500)
        low = rate(date(2026, 12, 1), date(2026, 12, 15), 1000, cleaning=300)
        q = quote(stay((2026, 12, 13), (2026, 12, 17)), [high, low])
        assert q is not None
        # 13 e 14 na baixa, 15 e 16 na alta; limpeza da tarifa da noite do check-in
        assert q.total_cents == (2 * 1000 + 2 * 1800 + 300) * R

    def test_last_night_before_rate_end_does_not_touch_next_rate(self) -> None:
        q = quote(stay((2026, 12, 26), (2026, 12, 28)), season(closed=True))
        assert q is not None
        assert q.total_cents == (2 * 1000 + 300) * R
        assert q.flags == frozenset()
        assert q.package is None

    def test_night_without_rate_is_not_quotable(self) -> None:
        assert quote(stay((2026, 11, 29), (2026, 12, 2)), [LOW]) is None

    def test_no_rates_at_all(self) -> None:
        assert quote(stay((2026, 12, 10), (2026, 12, 12)), []) is None


class TestPackage:
    def test_stay_covering_exactly_the_package(self) -> None:
        q = quote(stay((2026, 12, 28), (2027, 1, 2)), season(closed=True))
        assert q is not None
        assert q.total_cents == (15000 + 400) * R
        assert q.flags == frozenset()
        assert q.package is not None
        pkg = q.package
        assert (pkg.name, pkg.start_date, pkg.end_date, pkg.total_cents) == (
            "Réveillon", date(2026, 12, 28), date(2027, 1, 2), 15000 * R,
        )  # fmt: skip

    def test_package_plus_nights_outside(self) -> None:
        q = quote(stay((2026, 12, 26), (2027, 1, 4)), season(closed=True))
        assert q is not None
        # 2 noites de baixa + pacote + 2 noites de janeiro + limpeza da baixa (check-in)
        assert q.total_cents == (2 * 1000 + 15000 + 2 * 1500 + 300) * R
        assert q.flags == frozenset()

    def test_partial_closed_package_requires_full_package(self) -> None:
        q = quote(stay((2026, 12, 30), (2027, 1, 2)), season(closed=True))
        assert q is not None
        assert Flag.REQUIRES_FULL_PACKAGE in q.flags
        assert q.package is not None
        assert (q.package.start_date, q.package.end_date) == (date(2026, 12, 28), date(2027, 1, 2))
        # estimativa considera o pacote completo (é o que a casa aluga)
        assert q.total_cents == (15000 + 400) * R

    def test_partial_closed_package_keeps_outside_nights(self) -> None:
        q = quote(stay((2027, 1, 1), (2027, 1, 5)), season(closed=True))
        assert q is not None
        assert Flag.REQUIRES_FULL_PACKAGE in q.flags
        assert q.total_cents == (15000 + 3 * 1500 + 400) * R

    def test_partial_open_package_uses_daily_rates(self) -> None:
        q = quote(stay((2026, 12, 30), (2027, 1, 2)), season(closed=False))
        assert q is not None
        assert Flag.REQUIRES_FULL_PACKAGE not in q.flags
        assert q.total_cents == (3 * 2000 + 400) * R
        assert q.package is None


class TestMinimumNights:
    def test_below_minimum(self) -> None:
        q = quote(stay((2026, 12, 10), (2026, 12, 11)), [LOW])
        assert q is not None
        assert q.flags == frozenset({Flag.BELOW_MIN_NIGHTS})
        assert q.min_nights_required == 2
        assert q.total_cents == (1000 + 300) * R

    def test_exactly_minimum(self) -> None:
        q = quote(stay((2026, 12, 10), (2026, 12, 12)), [LOW])
        assert q is not None
        assert q.flags == frozenset()

    def test_uses_largest_minimum_of_touched_periods(self) -> None:
        q = quote(stay((2026, 12, 27), (2026, 12, 29)), season(closed=False))
        assert q is not None
        assert Flag.BELOW_MIN_NIGHTS in q.flags
        assert q.min_nights_required == 5

    def test_closed_partial_package_can_have_both_flags(self) -> None:
        q = quote(stay((2026, 12, 30), (2027, 1, 2)), season(closed=True))
        assert q is not None
        assert q.flags == frozenset({Flag.REQUIRES_FULL_PACKAGE, Flag.BELOW_MIN_NIGHTS})
