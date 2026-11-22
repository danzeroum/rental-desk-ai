"""Horário dos corretores e tempo comercial (RF12, RF24, RF29). Puro, sem I/O.

O escalonamento conta só minutos dentro do horário: fora dele os relógios param, e
uma passagem criada à noite começa a contar quando o plantão abre (esclarecimento
do time, SPEC RF12).

Formato guardado em `tenant.broker_hours`: dia da semana (0 = segunda … 6 =
domingo) → lista de turnos ["HH:MM", "HH:MM"], fim exclusivo, fuso America/Sao_Paulo.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from app.core.config import TIMEZONE

DEFAULT_HOURS: Final[dict[str, list[list[str]]]] = {
    str(day): [["09:00", "18:00"]] for day in range(7)
}
"""Usado enquanto a imobiliária não configurar o horário (D032)."""

WEEKDAYS: Final = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado",
    "domingo",
)  # fmt: skip
_HHMM: Final = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SEARCH_DAYS: Final = 8
_MAX_DAYS: Final = 2 * 366


class InvalidHoursError(ValueError):
    pass


def _parse_time(value: Any) -> time:
    match = _HHMM.match(value) if isinstance(value, str) else None
    if match is None:
        raise InvalidHoursError(f"horário inválido: {value!r} (use HH:MM)")
    return time(int(match.group(1)), int(match.group(2)))


@dataclass(frozen=True)
class BusinessHours:
    shifts: tuple[tuple[tuple[time, time], ...], ...]
    """Índice = dia da semana (0 = segunda)."""

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> BusinessHours:
        source = raw or DEFAULT_HOURS
        days: list[list[tuple[time, time]]] = [[] for _ in range(7)]
        for key, shifts in source.items():
            if key not in {str(d) for d in range(7)} or not isinstance(shifts, list):
                raise InvalidHoursError(f"dia inválido: {key!r}")
            for shift in shifts:
                if not isinstance(shift, list) or len(shift) != 2:  # noqa: PLR2004
                    raise InvalidHoursError("cada turno é [início, fim]")
                start, end = _parse_time(shift[0]), _parse_time(shift[1])
                if end <= start:
                    raise InvalidHoursError("o fim do turno precisa ser depois do início")
                days[int(key)].append((start, end))
        for day in days:
            day.sort()
            for (_, prev_end), (next_start, _) in itertools.pairwise(day):
                if next_start < prev_end:
                    raise InvalidHoursError("turnos sobrepostos no mesmo dia")
        return cls(tuple(tuple(day) for day in days))

    def to_json(self) -> dict[str, list[list[str]]]:
        return {
            str(day): [[s.strftime("%H:%M"), e.strftime("%H:%M")] for s, e in shifts]
            for day, shifts in enumerate(self.shifts)
            if shifts
        }

    def _windows(self, start_day: date, days: int) -> list[tuple[datetime, datetime]]:
        windows = []
        for offset in range(days):
            day = start_day + timedelta(days=offset)
            for begin, end in self.shifts[day.weekday()]:
                windows.append(
                    (
                        datetime.combine(day, begin, tzinfo=TIMEZONE),
                        datetime.combine(day, end, tzinfo=TIMEZONE),
                    )
                )
        return windows

    def is_open(self, moment: datetime) -> bool:
        local = moment.astimezone(TIMEZONE)
        return any(start <= local < end for start, end in self._windows(local.date(), 1))

    def next_opening(self, moment: datetime) -> datetime:
        """O próprio instante se estiver aberto; senão, o início do próximo turno."""
        local = moment.astimezone(TIMEZONE)
        for start, end in self._windows(local.date(), _SEARCH_DAYS):
            if local < end:
                return max(start, local)
        raise InvalidHoursError("nenhum turno configurado na semana")

    def elapsed(self, start: datetime, end: datetime) -> timedelta:
        """Tempo comercial entre dois instantes."""
        if end <= start:
            return timedelta(0)
        local_start, local_end = start.astimezone(TIMEZONE), end.astimezone(TIMEZONE)
        days = (local_end.date() - local_start.date()).days + 1
        total = timedelta(0)
        for w_start, w_end in self._windows(local_start.date(), days):
            overlap = min(w_end, local_end) - max(w_start, local_start)
            if overlap > timedelta(0):
                total += overlap
        return total

    def add(self, start: datetime, duration: timedelta) -> datetime:
        """Instante em que `duration` de tempo comercial terá passado desde `start`."""
        if not any(self.shifts):
            raise InvalidHoursError("nenhum turno configurado na semana")
        remaining = duration
        cursor = start.astimezone(TIMEZONE)
        day = cursor.date()
        for _ in range(_MAX_DAYS):
            for w_start, w_end in self._windows(day, 1):
                if w_end <= cursor:
                    continue
                begin = max(w_start, cursor)
                if begin + remaining <= w_end:
                    return begin + remaining
                remaining -= w_end - begin
                cursor = w_end
            day += timedelta(days=1)
        raise InvalidHoursError("prazo além de dois anos de horário comercial")

    def describe_next_opening(self, moment: datetime) -> str:
        """ "hoje a partir das 9h", "amanhã a partir das 8h30", "segunda-feira a partir das 9h"."""
        opening = self.next_opening(moment)
        local_now = moment.astimezone(TIMEZONE)
        days = (opening.date() - local_now.date()).days
        when = "hoje" if days == 0 else "amanhã" if days == 1 else WEEKDAYS[opening.weekday()]
        clock = f"{opening.hour}h" + (f"{opening.minute:02d}" if opening.minute else "")
        return f"{when} a partir das {clock}"
