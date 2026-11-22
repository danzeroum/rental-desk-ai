"""Horário dos corretores (RF12, RF24) em tempo comercial, fuso America/Sao_Paulo."""

from datetime import datetime, timedelta

import pytest

from app.core.config import TIMEZONE
from app.domain.hours import DEFAULT_HOURS, BusinessHours, InvalidHoursError

# Segunda a sexta 9h-18h; sábado 9h-13h; domingo fechado.
WEEK = {
    "0": [["09:00", "18:00"]], "1": [["09:00", "18:00"]], "2": [["09:00", "18:00"]],
    "3": [["09:00", "18:00"]], "4": [["09:00", "18:00"]], "5": [["09:00", "13:00"]],
}  # fmt: skip
HOURS = BusinessHours.from_json(WEEK)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """`day` em novembro de 2026: 23 = segunda-feira."""
    return datetime(2026, 11, day, hour, minute, tzinfo=TIMEZONE)


class TestIsOpen:
    def test_inside_and_outside(self) -> None:
        assert HOURS.is_open(at(23, 9))
        assert HOURS.is_open(at(23, 17, 59))
        assert not HOURS.is_open(at(23, 18))  # fim exclusivo
        assert not HOURS.is_open(at(23, 8, 59))
        assert not HOURS.is_open(at(29, 10))  # domingo

    def test_converts_utc_to_sao_paulo(self) -> None:
        utc_noon = datetime(2026, 11, 23, 12, 0, tzinfo=TIMEZONE).astimezone(tz=None)
        assert HOURS.is_open(utc_noon)


class TestNextOpening:
    def test_open_now_returns_now(self) -> None:
        assert HOURS.next_opening(at(23, 10)) == at(23, 10)

    def test_before_opening_same_day(self) -> None:
        assert HOURS.next_opening(at(23, 7)) == at(23, 9)

    def test_after_closing_next_day(self) -> None:
        assert HOURS.next_opening(at(23, 19)) == at(24, 9)

    def test_saturday_afternoon_skips_sunday(self) -> None:
        assert HOURS.next_opening(at(28, 14)) == at(30, 9)


class TestBusinessElapsed:
    def test_inside_one_shift(self) -> None:
        assert HOURS.elapsed(at(23, 10), at(23, 10, 15)) == timedelta(minutes=15)

    def test_pauses_overnight(self) -> None:
        # 17h50 → 9h05 do dia seguinte: 10 min de ontem + 5 min de hoje
        assert HOURS.elapsed(at(23, 17, 50), at(24, 9, 5)) == timedelta(minutes=15)

    def test_created_out_of_hours_counts_from_opening(self) -> None:
        assert HOURS.elapsed(at(23, 22), at(24, 9, 10)) == timedelta(minutes=10)

    def test_reversed_interval_is_zero(self) -> None:
        assert HOURS.elapsed(at(23, 11), at(23, 10)) == timedelta(0)

    def test_long_deadline_crosses_weekend(self) -> None:
        # sexta 17h + 5h comerciais: 1h na sexta, 4h no sábado (9h-13h) → segunda 9h fecha
        assert HOURS.add(at(27, 17), timedelta(hours=5)) == at(28, 13)
        assert HOURS.add(at(27, 17), timedelta(hours=6)) == at(30, 10)
        assert HOURS.elapsed(at(27, 17), at(30, 10)) == timedelta(hours=6)

    def test_deadline_in_business_time(self) -> None:
        # 15 min comerciais a partir de 17h50 de sexta caem às 9h05 de sábado
        assert HOURS.add(at(27, 17, 50), timedelta(minutes=15)) == at(28, 9, 5)


class TestDescribe:
    def test_today_tomorrow_weekday(self) -> None:
        assert HOURS.describe_next_opening(at(23, 7)) == "hoje a partir das 9h"
        assert HOURS.describe_next_opening(at(23, 19)) == "amanhã a partir das 9h"
        assert HOURS.describe_next_opening(at(28, 14)) == "segunda-feira a partir das 9h"

    def test_minutes_when_not_round(self) -> None:
        hours = BusinessHours.from_json({str(d): [["08:30", "12:00"]] for d in range(7)})
        assert hours.describe_next_opening(at(23, 13)) == "amanhã a partir das 8h30"


class TestParsing:
    def test_empty_uses_default(self) -> None:
        assert BusinessHours.from_json({}) == BusinessHours.from_json(DEFAULT_HOURS)

    @pytest.mark.parametrize(
        "raw",
        [
            {"7": [["09:00", "18:00"]]},
            {"0": [["18:00", "09:00"]]},
            {"0": [["9h", "18h"]]},
            {"0": [["09:00", "13:00"], ["12:00", "18:00"]]},
            {"0": "09:00-18:00"},
        ],
    )
    def test_invalid(self, raw: dict[str, object]) -> None:
        with pytest.raises(InvalidHoursError):
            BusinessHours.from_json(raw)

    def test_split_shift(self) -> None:
        hours = BusinessHours.from_json({"0": [["09:00", "12:00"], ["14:00", "18:00"]]})
        assert not hours.is_open(at(23, 13))
        assert hours.next_opening(at(23, 13)) == at(23, 14)
        assert hours.to_json() == {"0": [["09:00", "12:00"], ["14:00", "18:00"]]}
