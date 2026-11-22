import io
import zipfile
from datetime import date

import pytest
from openpyxl import Workbook, load_workbook

from app.importer.analyze import analyze_workbook
from app.importer.model import Severity
from app.importer.sample import build_sample, carnival_tuesday, easter
from app.importer.schema import DEFAULT_RULE_KEYS, SHEETS
from app.importer.workbook import MAX_FILE_BYTES, build_workbook

TODAY = date(2026, 11, 21)


def test_template_has_the_four_sheets_and_headers() -> None:
    wb = load_workbook(io.BytesIO(build_workbook()))
    assert wb.sheetnames == ["Imoveis", "Tarifas", "Bloqueios", "Regras"]
    for sheet in SHEETS:
        header = [cell.value for cell in wb[sheet.name][1]]
        assert header == [c.name for c in sheet.columns]
        assert wb[sheet.name]["A1"].comment is not None


def test_template_validates_cells() -> None:
    wb = load_workbook(io.BytesIO(build_workbook()))
    imoveis = wb["Imoveis"].data_validations.dataValidation
    assert any(v.formula1 == '"Sim,Não"' for v in imoveis)
    tarifas = wb["Tarifas"].data_validations.dataValidation
    assert any(v.type == "date" for v in tarifas)
    assert any(v.formula1 and "Imoveis!" in v.formula1 for v in tarifas)


def test_template_suggests_rule_keys() -> None:
    wb = load_workbook(io.BytesIO(build_workbook()))
    keys = [row[0] for row in wb["Regras"].iter_rows(min_row=2, values_only=True) if row[0]]
    assert keys == list(DEFAULT_RULE_KEYS)


def test_empty_template_reports_missing_houses() -> None:
    report = analyze_workbook(build_workbook(), today=TODAY)
    assert [i.message for i in report.errors] == [
        "nenhuma casa cadastrada: preencha ao menos uma linha"
    ]


def test_sample_roundtrip_imports_without_errors() -> None:
    sample = build_sample(2026)
    report = analyze_workbook(build_workbook(sample), today=TODAY)
    assert report.ok, [i.describe() for i in report.errors]
    assert report.warnings == []
    assert len(report.data.properties) == 10
    assert report.data.content_hash() == sample.content_hash()


def test_sample_has_closed_and_open_reveillon_packages() -> None:
    reveillon = [r for r in build_sample(2026).rates if r.package_name == "Réveillon"]
    assert {r.package_closed for r in reveillon} == {True, False}
    assert all(
        (r.start_date, r.end_date) == (date(2026, 12, 28), date(2027, 1, 2)) for r in reveillon
    )


@pytest.mark.parametrize(
    ("year", "expected"), [(2026, date(2026, 4, 5)), (2027, date(2027, 3, 28))]
)
def test_easter(year: int, expected: date) -> None:
    assert easter(year) == expected


def test_carnival_2027() -> None:
    assert carnival_tuesday(2027) == date(2027, 2, 9)


def test_not_a_spreadsheet() -> None:
    report = analyze_workbook(b"isto nao e um xlsx", today=TODAY)
    assert [(i.sheet, i.severity) for i in report.issues] == [("arquivo", Severity.ERROR)]


def test_file_too_large() -> None:
    report = analyze_workbook(b"0" * (MAX_FILE_BYTES + 1), today=TODAY)
    assert "grande demais" in report.errors[0].message


def test_zip_bomb_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"0" * (101 * 1024 * 1024))
    report = analyze_workbook(buffer.getvalue(), today=TODAY)
    assert "grande demais" in report.errors[0].message


def test_header_matching_ignores_accents_and_case_and_reports_structure() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Imóveis"
    ws.append(["Código", "Título_Curto", "capacidade_max", "cor_da_porta"])
    ws.append(["casa01", "Casa", 4, "azul"])
    out = io.BytesIO()
    wb.save(out)
    report = analyze_workbook(out.getvalue(), today=TODAY)
    messages = [i.describe() for i in report.issues]
    assert "Imoveis, linha 1: coluna 'cor_da_porta' desconhecida, ignorada" in messages
    assert "Tarifas: aba 'Tarifas' não encontrada" in messages
    assert report.data.properties[0].code == "CASA01"


def test_errors_point_to_sheet_row_and_column() -> None:
    sample = build_sample(2026)
    wb = load_workbook(io.BytesIO(build_workbook(sample)))
    wb["Tarifas"]["D3"] = "a combinar"
    out = io.BytesIO()
    wb.save(out)
    report = analyze_workbook(out.getvalue(), today=TODAY)
    assert [i.describe() for i in report.errors] == [
        "Tarifas, linha 3, coluna diaria: valor inválido: use só números, ex.: 1500,00"
    ]


def test_generated_sheet_uses_last_night_and_checkout_columns() -> None:
    wb = load_workbook(io.BytesIO(build_workbook(build_sample(2026))))
    header = [c.value for c in wb["Tarifas"][1]]
    assert header[1:3] == ["primeira_noite", "ultima_noite"]
    reveillon = next(
        row for row in wb["Tarifas"].iter_rows(min_row=2, values_only=True) if row[5] == "Réveillon"
    )
    assert (reveillon[1].date(), reveillon[2].date()) == (date(2026, 12, 28), date(2027, 1, 1))
    assert [c.value for c in wb["Bloqueios"][1]] == ["codigo", "entrada", "saida"]
    block = next(wb["Bloqueios"].iter_rows(min_row=2, values_only=True))
    assert (block[1].date(), block[2].date()) == (date(2026, 12, 28), date(2027, 1, 2))
