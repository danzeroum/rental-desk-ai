from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.importer.__main__ import main


def test_template_command(tmp_path: Path) -> None:
    out = tmp_path / "modelo.xlsx"
    assert main(["template", "--out", str(out)]) == 0
    assert load_workbook(out).sheetnames == ["Imoveis", "Tarifas", "Bloqueios", "Regras"]


def test_sample_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "exemplo.xlsx"
    assert main(["sample", "--season", "2030", "--out", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    assert "Planilha válida" in capsys.readouterr().out


def test_check_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "modelo.xlsx"
    main(["template", "--out", str(out)])
    assert main(["check", str(out)]) == 1
    assert "ERRO   Imoveis: nenhuma casa cadastrada" in capsys.readouterr().out
