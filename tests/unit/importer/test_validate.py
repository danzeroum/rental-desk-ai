from datetime import date
from typing import Any

import pytest

from app.importer.model import ImportReport, Issue, RawRow, Severity
from app.importer.validate import validate_workbook

TODAY = date(2026, 11, 21)


def house(**overrides: Any) -> dict[str, object]:
    row: dict[str, object] = {
        "codigo": "CASA01",
        "titulo_curto": "Casa Azul",
        "capacidade_max": 10,
        "quartos": 4,
        "suites": 2,
        "piscina": "Sim",
        "aceita_pet": "Não",
        "wifi": None,
    }
    row.update(overrides)
    return row


def rate(**overrides: Any) -> dict[str, object]:
    row: dict[str, object] = {
        "codigo": "CASA01",
        "inicio": date(2026, 12, 1),
        "fim": date(2026, 12, 28),
        "diaria": 1500,
    }
    row.update(overrides)
    return row


def rows(*values: dict[str, object]) -> list[RawRow]:
    return [RawRow(number=i + 2, values=v) for i, v in enumerate(values)]


def run(
    imoveis: list[dict[str, object]] | None = None,
    tarifas: list[dict[str, object]] | None = None,
    bloqueios: list[dict[str, object]] | None = None,
    regras: list[dict[str, object]] | None = None,
) -> ImportReport:
    raw = {
        "Imoveis": rows(*(imoveis if imoveis is not None else [house()])),
        "Tarifas": rows(*(tarifas if tarifas is not None else [rate()])),
        "Bloqueios": rows(*(bloqueios or [])),
        "Regras": rows(*(regras or [])),
    }
    return validate_workbook(raw, today=TODAY)


def only(issues: list[Issue]) -> Issue:
    assert len(issues) == 1, [i.describe() for i in issues]
    return issues[0]


class TestValidWorkbook:
    def test_normalizes_values(self) -> None:
        report = run(
            tarifas=[
                rate(),
                rate(
                    inicio=date(2026, 12, 28),
                    fim=date(2027, 1, 2),
                    diaria="2.000,00",
                    minimo_noites=5,
                    pacote_nome="Réveillon",
                    pacote_valor="R$ 15.000,00",
                    pacote_fechado="Sim",
                    taxa_limpeza=350,
                ),
            ],
            bloqueios=[{"codigo": "casa01", "inicio": "10/12/2026", "fim": "15/12/2026"}],
            regras=[{"chave": "check-in", "valor": "a partir das 14h"}],
        )
        assert report.ok, [i.describe() for i in report.errors]
        prop = only_prop = report.data.properties[0]
        assert (prop.code, prop.max_guests, prop.has_pool, prop.accepts_pets, prop.has_wifi) == (
            "CASA01",
            10,
            True,
            False,
            None,
        )
        assert only_prop.title == "Casa Azul"
        low, reveillon = report.data.rates
        assert (low.daily_cents, low.min_nights, low.cleaning_fee_cents, low.package_closed) == (
            150_000,
            1,
            0,
            False,
        )
        assert reveillon.end_date == date(2027, 1, 2)  # fim = dia da saída (exclusivo)
        assert (reveillon.package_cents, reveillon.package_closed) == (1_500_000, True)
        assert report.data.blocks[0].property_code == "CASA01"
        assert report.data.rules == {"check-in": "a partir das 14h"}

    def test_blank_rows_are_ignored(self) -> None:
        report = run(imoveis=[house(), {"codigo": None, "titulo_curto": "  "}])
        assert report.ok
        assert len(report.data.properties) == 1

    def test_hash_is_stable_and_order_independent(self) -> None:
        a = run(imoveis=[house(), house(codigo="CASA02")], tarifas=[rate(), rate(codigo="CASA02")])
        b = run(imoveis=[house(codigo="CASA02"), house()], tarifas=[rate(codigo="CASA02"), rate()])
        assert a.data.content_hash() == b.data.content_hash()
        c = run(tarifas=[rate(diaria=1501)])
        assert c.data.content_hash() != run().data.content_hash()


class TestImoveisErrors:
    def test_no_properties(self) -> None:
        issue = only(run(imoveis=[], tarifas=[]).errors)
        assert issue.sheet == "Imoveis"
        assert "nenhuma casa" in issue.message

    def test_required_missing(self) -> None:
        issue = only(run(imoveis=[house(titulo_curto=None)]).errors)
        assert (issue.row, issue.column) == (2, "titulo_curto")
        assert "obrigatório" in issue.message

    def test_duplicate_code(self) -> None:
        issue = only(run(imoveis=[house(), house(codigo="casa01")]).errors)
        assert (issue.row, issue.column) == (3, "codigo")
        assert "repetido" in issue.message

    def test_bad_capacity(self) -> None:
        issue = only(run(imoveis=[house(capacidade_max=0)]).errors)
        assert issue.column == "capacidade_max"

    def test_bad_attribute(self) -> None:
        issue = only(run(imoveis=[house(piscina="talvez")]).errors)
        assert (issue.column, issue.row) == ("piscina", 2)

    def test_bad_photos_link(self) -> None:
        issue = only(run(imoveis=[house(link_fotos="drive.google.com/x")]).errors)
        assert issue.column == "link_fotos"


class TestTarifasErrors:
    def test_unknown_code(self) -> None:
        other = rate(codigo="CASA99", inicio=date(2027, 1, 5), fim=date(2027, 1, 9))
        issue = only(run(tarifas=[rate(), other]).errors)
        assert (issue.sheet, issue.row, issue.column) == ("Tarifas", 3, "codigo")
        assert "não existe na aba Imoveis" in issue.message

    @pytest.mark.parametrize("fim", [date(2026, 12, 1), date(2026, 11, 30)])
    def test_end_not_after_start(self, fim: date) -> None:
        issue = only(run(tarifas=[rate(fim=fim)]).errors)
        assert issue.column == "fim"
        assert "depois do início" in issue.message

    def test_non_numeric_rate(self) -> None:
        issue = only(run(tarifas=[rate(diaria="a combinar")]).errors)
        assert issue.column == "diaria"
        assert "valor" in issue.message

    def test_overlapping_periods(self) -> None:
        report = run(tarifas=[rate(), rate(inicio=date(2026, 12, 27), fim=date(2027, 1, 2))])
        issue = only(report.errors)
        assert (issue.row, issue.column) == (3, "inicio")
        assert "sobrepõe" in issue.message
        assert "linha 2" in issue.message

    def test_adjacent_periods_are_fine(self) -> None:
        report = run(tarifas=[rate(), rate(inicio=date(2026, 12, 28), fim=date(2027, 1, 2))])
        assert report.ok

    def test_package_value_without_name(self) -> None:
        issue = only(run(tarifas=[rate(pacote_valor=10000)]).errors)
        assert issue.column == "pacote_nome"

    def test_package_name_without_value(self) -> None:
        issue = only(run(tarifas=[rate(pacote_nome="Réveillon")]).errors)
        assert issue.column == "pacote_valor"

    def test_closed_flag_without_package(self) -> None:
        issue = only(run(tarifas=[rate(pacote_fechado="Sim")]).errors)
        assert issue.column == "pacote_fechado"

    def test_min_nights_at_least_one(self) -> None:
        issue = only(run(tarifas=[rate(minimo_noites=0)]).errors)
        assert issue.column == "minimo_noites"


class TestBloqueiosErrors:
    def test_unknown_code(self) -> None:
        bloq = {"codigo": "CASA99", "inicio": date(2026, 12, 1), "fim": date(2026, 12, 3)}
        issue = only(run(bloqueios=[bloq]).errors)
        assert (issue.sheet, issue.column) == ("Bloqueios", "codigo")

    def test_same_day_in_and_out(self) -> None:
        bloq = {"codigo": "CASA01", "inicio": date(2026, 12, 1), "fim": date(2026, 12, 1)}
        issue = only(run(bloqueios=[bloq]).errors)
        assert issue.column == "fim"


class TestRegras:
    def test_empty_value_is_skipped(self) -> None:
        report = run(regras=[{"chave": "caução", "valor": None}])
        assert report.ok
        assert report.data.rules == {}

    def test_value_without_key(self) -> None:
        issue = only(run(regras=[{"chave": None, "valor": "texto solto"}]).errors)
        assert issue.column == "chave"

    def test_duplicate_key(self) -> None:
        regras: list[dict[str, object]] = [
            {"chave": "Check-in", "valor": "14h"},
            {"chave": "check-in", "valor": "15h"},
        ]
        issue = only(run(regras=regras).errors)
        assert (issue.row, issue.column) == (3, "chave")


class TestWarnings:
    def test_house_without_future_rate(self) -> None:
        past = rate(inicio=date(2026, 1, 1), fim=date(2026, 2, 1))
        warning = only(run(tarifas=[past]).warnings)
        assert warning.severity is Severity.WARNING
        assert "sem tarifa a partir de hoje" in warning.message

    def test_rate_gap(self) -> None:
        tarifas = [rate(), rate(inicio=date(2026, 12, 29), fim=date(2027, 1, 2))]
        warning = only(run(tarifas=tarifas).warnings)
        assert (warning.sheet, warning.row) == ("Tarifas", 3)
        assert "28/12/2026" in warning.message
        assert "sem tarifa" in warning.message

    def test_more_suites_than_bedrooms(self) -> None:
        warning = only(run(imoveis=[house(suites=5)]).warnings)
        assert warning.column == "suites"

    def test_capacity_high_for_bedrooms(self) -> None:
        warning = only(run(imoveis=[house(capacidade_max=30, quartos=4)]).warnings)
        assert warning.column == "capacidade_max"

    def test_warnings_do_not_block(self) -> None:
        report = run(imoveis=[house(suites=5)])
        assert report.ok
