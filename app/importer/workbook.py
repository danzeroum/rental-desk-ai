"""Arquivo .xlsx: gerar o modelo (com validação de células) e ler o enviado.

Leitura defensiva: o arquivo vem de fora. Limites de tamanho, de descompactação
e de linhas; XML lido com defusedxml (o openpyxl usa automaticamente quando
instalado).
"""

from __future__ import annotations

import zipfile
from datetime import date
from io import BytesIO
from typing import Any, Final

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.importer.model import Issue, RawRow, Severity, WorkbookData
from app.importer.schema import (
    BLOQUEIOS,
    DEFAULT_RULE_KEYS,
    IMOVEIS,
    REGRAS,
    SHEETS,
    TARIFAS,
    Column,
    Kind,
    Sheet,
    normalize_name,
)

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES: Final = 100 * 1024 * 1024
MAX_ZIP_ENTRIES: Final = 500
REQUIRED_SHEETS: Final = (IMOVEIS.name, TARIFAS.name)
TEMPLATE_ROWS: Final = 1000
XLSX_MIME: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_HEADER_FONT: Final = Font(bold=True, color="FFFFFF")
_HEADER_FILL: Final = PatternFill("solid", fgColor="1F4E79")
_REQUIRED_FILL: Final = PatternFill("solid", fgColor="C00000")


class WorkbookError(ValueError):
    """Arquivo que não pode nem ser aberto como planilha."""


# --- Geração ----------------------------------------------------------------------


def build_workbook(data: WorkbookData | None = None) -> bytes:
    """Planilha modelo vazia, ou preenchida com `data` (exemplo e demonstração)."""
    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    for sheet in SHEETS:
        ws = wb.create_sheet(sheet.name)
        _write_header(ws, sheet)
        _add_validations(ws, sheet)
    _fill(wb, data)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _write_header(ws: Worksheet, sheet: Sheet) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        cell = ws.cell(row=1, column=index, value=column.name)
        cell.font = _HEADER_FONT
        cell.fill = _REQUIRED_FILL if column.required else _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        note = ("Obrigatório. " if column.required else "") + column.help
        if index == 1:
            note = f"{sheet.help}\n\n{note}"
        cell.comment = Comment(note, "rental-desk-ai", width=320, height=140)
        width = max(len(column.name) + 4, 14 if column.kind is not Kind.TEXT else 28)
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A2"


def _validation_for(column: Column, sheet: Sheet) -> DataValidation | None:
    common: dict[str, Any] = {"allow_blank": True, "showErrorMessage": True}
    match column.kind:
        case Kind.BOOL:
            return DataValidation(
                type="list", formula1='"Sim,Não"', error="Use Sim ou Não.", **common
            )
        case Kind.INT:
            return DataValidation(
                type="whole",
                operator="greaterThanOrEqual",
                formula1=str(column.min_value),
                error=f"Use um número inteiro a partir de {column.min_value}.",
                **common,
            )
        case Kind.MONEY:
            return DataValidation(
                type="decimal",
                operator="greaterThanOrEqual",
                formula1="0",
                error="Use um valor em reais, ex.: 1500,00.",
                **common,
            )
        case Kind.DATE:
            return DataValidation(
                type="date",
                operator="between",
                formula1="DATE(2020,1,1)",
                formula2="DATE(2100,12,31)",
                error="Use uma data no formato dd/mm/aaaa.",
                **common,
            )
        case Kind.CODE if sheet is not IMOVEIS:
            last = TEMPLATE_ROWS + 1
            return DataValidation(
                type="list",
                formula1=f"={IMOVEIS.name}!$A$2:$A${last}",
                error="Use um código da aba Imoveis.",
                **common,
            )
        case _:
            return None


def _add_validations(ws: Worksheet, sheet: Sheet) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        letter = get_column_letter(index)
        cells = f"{letter}2:{letter}{TEMPLATE_ROWS + 1}"
        validation = _validation_for(column, sheet)
        if validation is not None:
            ws.add_data_validation(validation)
            validation.add(cells)
        if column.kind is Kind.DATE:
            for row in range(2, TEMPLATE_ROWS + 2):
                ws[f"{letter}{row}"].number_format = "DD/MM/YYYY"
        elif column.kind is Kind.MONEY:
            for row in range(2, TEMPLATE_ROWS + 2):
                ws[f"{letter}{row}"].number_format = '"R$" #,##0.00'


def _yes_no(value: bool | None) -> str | None:
    return None if value is None else ("Sim" if value else "Não")


def _reais(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


CellValue = str | int | float | date | None


def _write_rows(ws: Worksheet, rows: list[list[CellValue]]) -> None:
    """Grava a partir da linha 2 (o `append` iria para depois das linhas já formatadas)."""
    for row_index, values in enumerate(rows, start=2):
        for column_index, value in enumerate(values, start=1):
            ws.cell(row=row_index, column=column_index, value=value)


def _fill(wb: Workbook, data: WorkbookData | None) -> None:
    rules = wb[REGRAS.name]
    if data is None:
        for row, key in enumerate(DEFAULT_RULE_KEYS, start=2):
            rules.cell(row=row, column=1, value=key)
        return
    imoveis, tarifas, bloqueios = wb[IMOVEIS.name], wb[TARIFAS.name], wb[BLOQUEIOS.name]
    _write_rows(imoveis, [
            [
                p.code, p.title, p.neighborhood, p.photos_url, p.max_guests, p.bedrooms,
                p.suites, p.bathrooms, p.parking_spots, _yes_no(p.has_pool),
                _yes_no(p.accepts_pets), _yes_no(p.allows_parties), _yes_no(p.ac_in_bedrooms),
                _yes_no(p.has_wifi), _yes_no(p.has_barbecue), p.beach_distance_m, p.public_notes,
            ]
            for p in data.properties
    ])  # fmt: skip
    _write_rows(tarifas, [
            [
                r.property_code, r.start_date, r.end_date, _reais(r.daily_cents), r.min_nights,
                r.package_name, _reais(r.package_cents),
                _yes_no(r.package_closed) if r.package_name else None,
                _reais(r.cleaning_fee_cents),
            ]
            for r in data.rates
    ])  # fmt: skip
    _write_rows(bloqueios, [[b.property_code, b.start_date, b.end_date] for b in data.blocks])
    _write_rows(rules, [[key, value] for key, value in data.rules.items()])


# --- Leitura ---------------------------------------------------------------------


def _check_container(content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise WorkbookError(f"arquivo grande demais (máximo {MAX_FILE_BYTES // 1024 // 1024} MB)")
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        raise WorkbookError("o arquivo não é uma planilha .xlsx válida") from None
    if len(entries) > MAX_ZIP_ENTRIES or (
        sum(e.file_size for e in entries) > MAX_UNCOMPRESSED_BYTES
    ):
        raise WorkbookError("o arquivo não é uma planilha .xlsx válida (conteúdo grande demais)")


def _header_map(sheet: Sheet, header: tuple[object, ...], issues: list[Issue]) -> dict[int, str]:
    known = {normalize_name(c.name): c.name for c in sheet.columns}
    mapping: dict[int, str] = {}
    for index, title in enumerate(header):
        if title is None or not str(title).strip():
            continue
        name = known.get(normalize_name(str(title)))
        if name is None:
            issues.append(
                Issue(Severity.WARNING, sheet.name, f"coluna '{title}' desconhecida, ignorada", 1)
            )
        else:
            mapping[index] = name
    for column in sheet.columns:
        if column.required and column.name not in mapping.values():
            issues.append(
                Issue(
                    Severity.ERROR,
                    sheet.name,
                    f"coluna obrigatória '{column.name}' não encontrada na linha 1",
                    1,
                    column.name,
                )
            )
    return mapping


def _read_sheet(ws: Any, sheet: Sheet, issues: list[Issue]) -> list[RawRow]:
    rows = ws.iter_rows(values_only=True)
    header = next(rows, ())
    mapping = _header_map(sheet, tuple(header), issues)
    result: list[RawRow] = []
    for number, values in enumerate(rows, start=2):
        if number > sheet.max_rows + 1:
            if any(v is not None for v in values):
                issues.append(
                    Issue(
                        Severity.ERROR, sheet.name, f"mais de {sheet.max_rows} linhas preenchidas"
                    )
                )
                break
            continue
        result.append(
            RawRow(
                number=number,
                values={
                    name: values[i] if i < len(values) else None for i, name in mapping.items()
                },
            )
        )
    return result


def read_workbook(content: bytes) -> tuple[dict[str, list[RawRow]], list[Issue]]:
    """Linhas brutas por aba + problemas de estrutura (abas e colunas)."""
    _check_container(content)
    try:
        wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl levanta vários tipos para arquivos corrompidos
        raise WorkbookError("o arquivo não é uma planilha .xlsx válida") from exc
    issues: list[Issue] = []
    raw: dict[str, list[RawRow]] = {}
    try:
        by_name = {normalize_name(name): name for name in wb.sheetnames}
        for sheet in SHEETS:
            actual = by_name.get(normalize_name(sheet.name))
            if actual is None:
                severity = Severity.ERROR if sheet.name in REQUIRED_SHEETS else Severity.WARNING
                issues.append(Issue(severity, sheet.name, f"aba '{sheet.name}' não encontrada"))
                continue
            raw[sheet.name] = _read_sheet(wb[actual], sheet, issues)
    finally:
        wb.close()
    return raw, issues
