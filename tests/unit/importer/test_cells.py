from datetime import date, datetime

import pytest

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


class TestCode:
    @pytest.mark.parametrize(
        ("raw", "expected"), [("casa12", "CASA12"), (" CASA-07 ", "CASA-07"), (12, "12")]
    )
    def test_normalizes(self, raw: object, expected: str) -> None:
        assert parse_code(raw) == expected

    @pytest.mark.parametrize("raw", ["casa 12", "-CASA", "Ç12", "A" * 21, 12.5])
    def test_rejects_invalid(self, raw: object) -> None:
        with pytest.raises(CellError, match="código"):
            parse_code(raw)

    def test_empty_is_none(self) -> None:
        assert parse_code(None) is None
        assert parse_code("  ") is None


class TestText:
    def test_strips_and_empty_is_none(self) -> None:
        assert parse_text("  Casa Azul ", 120) == "Casa Azul"
        assert parse_text("", 120) is None
        assert parse_text(None, 120) is None

    def test_number_becomes_text(self) -> None:
        assert parse_text(14, 40) == "14"

    def test_too_long(self) -> None:
        with pytest.raises(CellError, match="no máximo 5"):
            parse_text("abcdef", 5)


class TestUrl:
    def test_accepts_http_and_https(self) -> None:
        assert parse_url("https://fotos.exemplo.com/casa12") == "https://fotos.exemplo.com/casa12"
        assert parse_url(" http://x.com/a ") == "http://x.com/a"

    @pytest.mark.parametrize(
        "raw",
        ["fotos.exemplo.com", "ftp://x.com", "javascript:alert(1)", "https://", "file:///etc"],
    )
    def test_rejects_other(self, raw: str) -> None:
        with pytest.raises(CellError, match="link"):
            parse_url(raw)


class TestInt:
    @pytest.mark.parametrize(("raw", "expected"), [(12, 12), (12.0, 12), (" 7 ", 7), ("0", 0)])
    def test_accepts(self, raw: object, expected: int) -> None:
        assert parse_int(raw, min_value=0) == expected

    @pytest.mark.parametrize("raw", [12.5, "doze", True, "1.5"])
    def test_rejects_non_integer(self, raw: object) -> None:
        with pytest.raises(CellError, match="número inteiro"):
            parse_int(raw, min_value=0)

    def test_minimum(self) -> None:
        with pytest.raises(CellError, match="pelo menos 1"):
            parse_int(0, min_value=1)


class TestBool:
    @pytest.mark.parametrize("raw", ["Sim", "sim", " SIM ", True, "S"])
    def test_true(self, raw: object) -> None:
        assert parse_bool(raw) is True

    @pytest.mark.parametrize("raw", ["Não", "nao", "NÃO", False, "N"])
    def test_false(self, raw: object) -> None:
        assert parse_bool(raw) is False

    @pytest.mark.parametrize("raw", [None, "", "  "])
    def test_empty_is_unknown(self, raw: object) -> None:
        assert parse_bool(raw) is None

    def test_rejects_other(self) -> None:
        with pytest.raises(CellError, match="Sim ou Não"):
            parse_bool("talvez")


class TestDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (datetime(2026, 12, 28, 0, 0), date(2026, 12, 28)),  # noqa: DTZ001 - célula do Excel
            (date(2027, 1, 2), date(2027, 1, 2)),
            ("28/12/2026", date(2026, 12, 28)),
            ("2/1/2027", date(2027, 1, 2)),
            ("2026-12-28", date(2026, 12, 28)),
        ],
    )
    def test_accepts(self, raw: object, expected: date) -> None:
        assert parse_date(raw) == expected

    @pytest.mark.parametrize("raw", ["31/02/2027", "28/12", "amanhã", 46000, "12/28/2026"])
    def test_rejects(self, raw: object) -> None:
        with pytest.raises(CellError, match="data"):
            parse_date(raw)


class TestMoney:
    @pytest.mark.parametrize(
        ("raw", "cents"),
        [
            (1500, 150_000),
            (1500.5, 150_050),
            (1500.25, 150_025),
            (0, 0),
            ("1500", 150_000),
            ("1.500", 150_000),
            ("1.500,00", 150_000),
            ("R$ 40.000,00", 4_000_000),
            ("1500,5", 150_050),
            ("1500.50", 150_050),
            ("12.345.678,90", 1_234_567_890),
        ],
    )
    def test_converts_to_cents(self, raw: object, cents: int) -> None:
        assert parse_money_cents(raw) == cents

    @pytest.mark.parametrize("raw", ["mil reais", "1.5.0", True])
    def test_rejects_non_numeric(self, raw: object) -> None:
        with pytest.raises(CellError, match="valor"):
            parse_money_cents(raw)

    def test_rejects_negative(self) -> None:
        with pytest.raises(CellError, match="negativo"):
            parse_money_cents(-10)

    @pytest.mark.parametrize("raw", [10.555, "10,555"])
    def test_rejects_fraction_of_cent(self, raw: object) -> None:
        with pytest.raises(CellError, match="centavos"):
            parse_money_cents(raw)

    def test_empty_is_none(self) -> None:
        assert parse_money_cents(None) is None
        assert parse_money_cents(" ") is None
