"""Resposta do gestor ao lembrete das 9h (RF16). Puro, sem I/O.

- "OK" renova a validade das datas (RF13).
- "CASA12 28/12-02/01" bloqueia a CASA12 com entrada 28/12 e saída 02/01; a noite da
  saída fica livre (mesma convenção da planilha, D016). Várias casas: uma por linha,
  ou separadas por ";" ou ",". O ano é opcional: sem ele, vale a próxima ocorrência a
  partir de hoje, e a saída antes da entrada vira o ano seguinte (Réveillon).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from app.domain.catalog import BlockData
from app.domain.render import br_date

MAX_BLOCK: Final = timedelta(days=366)
_OK: Final = re.compile(r"^\s*ok[\s.!]*$", re.IGNORECASE)
_DAY: Final = r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?"
_BLOCK: Final = re.compile(
    rf"^\s*([A-Za-z0-9_-]{{2,20}})\s*:?\s+{_DAY}\s*(?:-|\u2013|a|até)\s*{_DAY}\s*$", re.IGNORECASE
)
_SPLIT: Final = re.compile(r"[\n;,]+")


@dataclass
class ReminderReply:
    confirm: bool = False
    blocks: list[BlockData] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _year(raw: str) -> int | None:
    if not raw:
        return None
    return int(raw) + 2000 if len(raw) == 2 else int(raw)  # noqa: PLR2004


def _date(day: str, month: str, year: int) -> date:
    return date(year, int(month), int(day))


def _period(groups: tuple[str | None, ...], today: date) -> tuple[date, date]:
    d1, m1, y1, d2, m2, y2 = (g or "" for g in groups)  # dia e mês sempre presentes
    start_year = _year(y1)
    start = _date(d1, m1, start_year or today.year)
    if start_year is None and start < today:
        start = _date(d1, m1, today.year + 1)
    end_year = _year(y2)
    end = _date(d2, m2, end_year or start.year)
    if end_year is None and end <= start:
        end = _date(d2, m2, start.year + 1)
    return start, end


def parse_reply(text: str, *, today: date, codes: frozenset[str]) -> ReminderReply | None:
    """`None` quando a mensagem não parece resposta ao lembrete."""
    reply, matched = ReminderReply(), False
    for part in (p for p in _SPLIT.split(text) if p.strip()):
        if _OK.match(part):
            reply.confirm = matched = True
            continue
        found = _BLOCK.match(part)
        if found is None:
            continue
        matched = True
        code = found.group(1).upper()
        try:
            start, end = _period(found.groups()[1:], today)
        except ValueError:
            reply.problems.append(f"{part.strip()}: data inválida")
            continue
        if code not in codes:
            reply.problems.append(f"{code}: casa não encontrada na planilha aprovada")
        elif start < today:
            reply.problems.append(f"{code}: a entrada {br_date(start)} já passou")
        elif end <= start or end - start > MAX_BLOCK:
            reply.problems.append(f"{code}: a saída precisa ser depois da entrada (até 1 ano)")
        else:
            reply.blocks.append(BlockData(property_code=code, start_date=start, end_date=end))
    return reply if matched else None
