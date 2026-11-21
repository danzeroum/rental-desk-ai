"""Leitura + validação de um arquivo enviado, sem tocar no banco."""

from __future__ import annotations

from datetime import date

from app.importer.model import ImportReport, Issue, Severity, WorkbookData
from app.importer.validate import validate_workbook
from app.importer.workbook import WorkbookError, read_workbook


def analyze_workbook(content: bytes, *, today: date) -> ImportReport:
    """Relatório com dados normalizados, erros (bloqueiam) e avisos, por aba e linha."""
    try:
        raw, structure_issues = read_workbook(content)
    except WorkbookError as exc:
        return ImportReport(
            data=WorkbookData(), issues=[Issue(Severity.ERROR, "arquivo", str(exc))]
        )
    report = validate_workbook(raw, today=today)
    report.issues[:0] = structure_issues
    return report
