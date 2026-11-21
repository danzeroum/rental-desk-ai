"""Validação da planilha: linhas brutas → dados normalizados + erros e avisos.

Puro, sem I/O. Erros bloqueiam a importação (RF14); avisos aparecem no painel e
não bloqueiam. A checagem dos links de fotos (HTTP) é feita fora daqui.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from app.importer.cells import (
    CellError,
    parse_bool,
    parse_code,
    parse_date,
    parse_int,
    parse_money_cents,
    parse_text,
    parse_url,
)
from app.importer.model import (
    BlockData,
    ImportReport,
    Issue,
    PropertyData,
    RateData,
    RawRow,
    Severity,
    WorkbookData,
)
from app.importer.schema import BLOQUEIOS, IMOVEIS, REGRAS, TARIFAS, Column, Kind, Sheet

MAX_GUESTS_PER_BEDROOM: Final = 4
"""Acima disso a capacidade vira aviso (D017); não bloqueia."""


def _fmt(day: date) -> str:
    return day.strftime("%d/%m/%Y")


@dataclass
class _Issues:
    items: list[Issue] = field(default_factory=list)

    def error(
        self, sheet: str, message: str, row: int | None = None, column: str | None = None
    ) -> None:
        self.items.append(Issue(Severity.ERROR, sheet, message, row, column))

    def warning(
        self, sheet: str, message: str, row: int | None = None, column: str | None = None
    ) -> None:
        self.items.append(Issue(Severity.WARNING, sheet, message, row, column))


class _Row:
    """Lê as células de uma linha segundo o tipo da coluna, acumulando erros."""

    def __init__(self, sheet: Sheet, raw: RawRow, issues: _Issues) -> None:
        self.sheet, self.raw, self.issues = sheet, raw, issues
        self.failed = False

    def get(self, name: str) -> object:
        column = self.sheet.column(name)
        try:
            value = _parse(column, self.raw.values.get(name))
        except CellError as exc:
            self._fail(column.name, str(exc))
            return None
        if value is None and column.required:
            self._fail(column.name, "campo obrigatório vazio")
        return value

    def _fail(self, column: str, message: str) -> None:
        self.failed = True
        self.issues.error(self.sheet.name, message, self.raw.number, column)


def _parse(column: Column, value: object) -> object:
    match column.kind:
        case Kind.TEXT:
            return parse_text(value, column.max_length)
        case Kind.INT:
            return parse_int(value, min_value=column.min_value)
        case _:
            return _SIMPLE_PARSERS[column.kind](value)


_SIMPLE_PARSERS: Final[dict[Kind, Callable[[object], object]]] = {
    Kind.CODE: parse_code,
    Kind.URL: parse_url,
    Kind.BOOL: parse_bool,
    Kind.DATE: parse_date,
    Kind.MONEY: parse_money_cents,
}


def _is_blank(raw: RawRow) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in raw.values.values())


def _data_rows(rows: list[RawRow]) -> list[RawRow]:
    return [r for r in rows if not _is_blank(r)]


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _opt_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --- Imoveis --------------------------------------------------------------------


def _properties(
    rows: list[RawRow], issues: _Issues
) -> tuple[dict[str, tuple[PropertyData, int]], set[str]]:
    """Casas válidas e todos os códigos citados (para não acusar "código inexistente"
    nas outras abas quando o problema é outra coluna da casa)."""
    found: dict[str, tuple[PropertyData, int]] = {}
    seen: dict[str, int] = {}
    for raw in _data_rows(rows):
        row = _Row(IMOVEIS, raw, issues)
        values = {c.name: row.get(c.name) for c in IMOVEIS.columns}
        code = _opt_str(values["codigo"])
        if code is not None and code in seen:
            row._fail("codigo", f"código {code} repetido (já usado na linha {seen[code]})")
        elif code is not None:
            seen[code] = raw.number
        if row.failed or code is None:
            continue
        prop = PropertyData(
            code=code,
            title=str(values["titulo_curto"]),
            neighborhood=_opt_str(values["bairro_ou_modulo"]),
            photos_url=_opt_str(values["link_fotos"]),
            max_guests=int(str(values["capacidade_max"])),
            bedrooms=_opt_int(values["quartos"]),
            suites=_opt_int(values["suites"]),
            bathrooms=_opt_int(values["banheiros"]),
            parking_spots=_opt_int(values["vagas"]),
            has_pool=_opt_bool(values["piscina"]),
            accepts_pets=_opt_bool(values["aceita_pet"]),
            allows_parties=_opt_bool(values["permite_festa"]),
            ac_in_bedrooms=_opt_bool(values["ar_quartos"]),
            has_wifi=_opt_bool(values["wifi"]),
            has_barbecue=_opt_bool(values["churrasqueira"]),
            beach_distance_m=_opt_int(values["distancia_praia_m"]),
            public_notes=_opt_str(values["observacoes_publicas"]),
        )
        found[code] = (prop, raw.number)
        _warn_capacity(prop, raw.number, issues)
    if not seen:
        issues.error(IMOVEIS.name, "nenhuma casa cadastrada: preencha ao menos uma linha")
    return found, set(seen)


def _warn_capacity(prop: PropertyData, row: int, issues: _Issues) -> None:
    if prop.bedrooms is not None and prop.suites is not None and prop.suites > prop.bedrooms:
        issues.warning(
            IMOVEIS.name,
            f"{prop.code}: mais suítes ({prop.suites}) que quartos ({prop.bedrooms})",
            row,
            "suites",
        )
    if prop.bedrooms and prop.max_guests > MAX_GUESTS_PER_BEDROOM * prop.bedrooms:
        issues.warning(
            IMOVEIS.name,
            f"{prop.code}: capacidade de {prop.max_guests} pessoas parece alta para "
            f"{prop.bedrooms} quartos; confira",
            row,
            "capacidade_max",
        )


# --- Períodos (Tarifas e Bloqueios) -------------------------------------------------


def sheet_end_to_exclusive(end: date) -> date:
    """Converte o `fim` da planilha para a convenção do banco (fim exclusivo).

    Na planilha, `fim` é o dia da saída, como no check-out: já é exclusivo.
    Ponto único de conversão (D016).
    """
    return end


def _period(row: _Row, codes: set[str]) -> tuple[str, date, date] | None:
    code = _opt_str(row.get("codigo"))
    start = row.get("inicio")
    end = row.get("fim")
    if code is not None and code not in codes:
        row._fail("codigo", f"código {code} não existe na aba Imoveis")
    if isinstance(start, date) and isinstance(end, date):
        end = sheet_end_to_exclusive(end)
        if end <= start:
            row._fail("fim", "o fim (dia da saída) precisa ser depois do início")
    if row.failed or code is None or not isinstance(start, date) or not isinstance(end, date):
        return None
    return code, start, end


def _rates(rows: list[RawRow], codes: set[str], issues: _Issues) -> list[tuple[RateData, int]]:
    found: list[tuple[RateData, int]] = []
    for raw in _data_rows(rows):
        row = _Row(TARIFAS, raw, issues)
        period = _period(row, codes)
        daily = row.get("diaria")
        min_nights = row.get("minimo_noites")
        package_name = _opt_str(row.get("pacote_nome"))
        package_value = _opt_int(row.get("pacote_valor"))
        closed = row.get("pacote_fechado")
        cleaning = row.get("taxa_limpeza")
        if package_value is not None and package_name is None:
            row._fail("pacote_nome", "pacote com valor precisa de nome")
        if package_name is not None and package_value is None:
            row._fail("pacote_valor", "pacote sem valor: preencha pacote_valor")
        if closed is True and package_value is None and package_name is None:
            row._fail("pacote_fechado", "pacote_fechado só vale para linhas com pacote")
        if row.failed or period is None:
            continue
        code, start, end = period
        found.append(
            (
                RateData(
                    property_code=code,
                    start_date=start,
                    end_date=end,
                    daily_cents=int(str(daily)),
                    min_nights=_opt_int(min_nights) or 1,
                    package_name=package_name,
                    package_cents=package_value,
                    package_closed=closed is True,
                    cleaning_fee_cents=_opt_int(cleaning) or 0,
                ),
                raw.number,
            )
        )
    return found


def _check_rate_periods(rates: list[tuple[RateData, int]], issues: _Issues) -> None:
    """Sobreposição = erro (qual tarifa vale?); lacuna = aviso (casa não cotável)."""
    by_house: dict[str, list[tuple[RateData, int]]] = defaultdict(list)
    for item in rates:
        by_house[item[0].property_code].append(item)
    for items in by_house.values():
        items.sort(key=lambda item: item[0].start_date)
        for (prev, prev_row), (cur, cur_row) in itertools.pairwise(items):
            if cur.start_date < prev.end_date:
                issues.error(
                    TARIFAS.name,
                    f"{cur.property_code}: período sobrepõe o da linha {prev_row} "
                    f"({_fmt(prev.start_date)} a {_fmt(prev.end_date)})",
                    cur_row,
                    "inicio",
                )
            elif cur.start_date > prev.end_date:
                issues.warning(
                    TARIFAS.name,
                    f"{cur.property_code}: noites de {_fmt(prev.end_date)} a "
                    f"{_fmt(cur.start_date)} (saída) sem tarifa; a casa não será oferecida "
                    "nessas datas",
                    cur_row,
                    "inicio",
                )


def _blocks(rows: list[RawRow], codes: set[str], issues: _Issues) -> list[BlockData]:
    found: list[BlockData] = []
    for raw in _data_rows(rows):
        period = _period(_Row(BLOQUEIOS, raw, issues), codes)
        if period is not None:
            found.append(BlockData(*period))
    return found


# --- Regras -----------------------------------------------------------------------


def _rules(rows: list[RawRow], issues: _Issues) -> dict[str, str]:
    found: dict[str, tuple[str, int]] = {}
    for raw in _data_rows(rows):
        row = _Row(REGRAS, raw, issues)
        key_column, value_column = REGRAS.columns
        try:
            key = parse_text(raw.values.get("chave"), key_column.max_length)
            value = parse_text(raw.values.get("valor"), value_column.max_length)
        except CellError as exc:
            row._fail("valor", str(exc))
            continue
        if value is None:
            continue  # chave sugerida pelo modelo e não preenchida
        if key is None:
            row._fail("chave", "regra sem assunto: preencha a coluna chave")
            continue
        normalized = key.lower()
        if normalized in found:
            row._fail("chave", f"regra '{key}' repetida (linha {found[normalized][1]})")
            continue
        found[normalized] = (value, raw.number)
    return {key: value for key, (value, _) in found.items()}


def validate_workbook(raw: Mapping[str, list[RawRow]], *, today: date) -> ImportReport:
    issues = _Issues()
    properties, codes = _properties(list(raw.get(IMOVEIS.name, [])), issues)
    rates = _rates(list(raw.get(TARIFAS.name, [])), codes, issues)
    _check_rate_periods(rates, issues)
    blocks = _blocks(list(raw.get(BLOQUEIOS.name, [])), codes, issues)
    rules = _rules(list(raw.get(REGRAS.name, [])), issues)

    future = {r.property_code for r, _ in rates if r.end_date > today}
    for code, (_, row) in properties.items():
        if code not in future:
            issues.warning(
                IMOVEIS.name,
                f"{code}: sem tarifa a partir de hoje; a casa não será oferecida",
                row,
                "codigo",
            )

    data = WorkbookData(
        properties=[p for p, _ in properties.values()],
        rates=[r for r, _ in rates],
        blocks=blocks,
        rules=rules,
    )
    return ImportReport(data=data, issues=issues.items)
