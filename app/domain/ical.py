"""Leitura de calendário iCal (RF26): Airbnb, Booking, Vrbo ou sistema próprio. Puro.

Só o necessário para bloqueios: VEVENT com UID, DTSTART e DTEND. Dia inteiro
(`VALUE=DATE`) já vem com o fim exclusivo, como a nossa convenção (a noite da saída
fica livre). Com hora, vale a data local (São Paulo) do início e do fim: saída às 11h
de 02/01 libera a noite de 02/01. Eventos cancelados são ignorados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import TIMEZONE

MAX_EVENTS: Final = 5000
_DATE: Final = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATETIME: Final = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")


class CalendarError(ValueError):
    """Conteúdo que não é um calendário iCal utilizável (mensagem para o painel)."""


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    start: date
    end: date
    """Exclusivo: a noite de `end` fica livre."""


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in {" ", "\t"} and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    parsed = dict(p.split("=", 1) for p in params if "=" in p)
    return name.upper(), {k.upper(): v.strip('"') for k, v in parsed.items()}, value.strip()


def _to_local_date(value: str, params: dict[str, str]) -> date:
    if (found := _DATE.match(value)) is not None:
        return date(*map(int, found.groups()))
    if (found := _DATETIME.match(value)) is None:
        raise CalendarError(f"data em formato desconhecido: {value!r}")
    y, mo, d, h, mi, s, utc = found.groups()
    if utc:
        zone: tzinfo = UTC
    else:
        try:
            zone = ZoneInfo(params["TZID"]) if "TZID" in params else TIMEZONE
        except (ZoneInfoNotFoundError, ValueError):
            zone = TIMEZONE
    moment = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=zone)
    return moment.astimezone(TIMEZONE).date()


def parse_ics(text: str) -> list[CalendarEvent]:
    lines = _unfold(text)
    if not lines or lines[0].strip().upper() != "BEGIN:VCALENDAR":
        raise CalendarError("o link não devolveu um calendário iCal (BEGIN:VCALENDAR)")
    events: list[CalendarEvent] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in lines:
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {}
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            if (event := _event(current)) is not None:
                events.append(event)
            current = None
            if len(events) > MAX_EVENTS:
                raise CalendarError("calendário com eventos demais")
        elif current is not None and name in {"UID", "DTSTART", "DTEND", "STATUS", "DURATION"}:
            current[name] = (params, value)
    return events


def _event(fields: dict[str, tuple[dict[str, str], str]]) -> CalendarEvent | None:
    if "DTSTART" not in fields or fields.get("STATUS", ({}, ""))[1].upper() == "CANCELLED":
        return None
    start_params, start_raw = fields["DTSTART"]
    start = _to_local_date(start_raw, start_params)
    if "DTEND" in fields:
        end = _to_local_date(fields["DTEND"][1], fields["DTEND"][0])
    else:
        end = start + timedelta(days=1)  # evento sem fim: uma noite (RFC 5545 para dia inteiro)
    if end <= start:
        end = start + timedelta(days=1)  # entrada e saída no mesmo dia ainda ocupam a noite
    uid = fields.get("UID", ({}, ""))[1] or f"sem-uid-{start.isoformat()}-{end.isoformat()}"
    return CalendarEvent(uid=uid[:255], start=start, end=end)
