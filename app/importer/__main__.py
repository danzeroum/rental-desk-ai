"""Linha de comando da planilha.

python -m app.importer template --out planilha-modelo.xlsx
python -m app.importer sample --season 2026 --out exemplo-demo-riviera.xlsx
python -m app.importer check arquivo.xlsx [--links]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from app.adapters.link_checker import FakeLinkChecker, HttpLinkChecker, LinkChecker
from app.core.config import TIMEZONE
from app.importer.model import ImportReport
from app.importer.sample import build_sample
from app.importer.service import prepare_import
from app.importer.workbook import build_workbook


def _today() -> datetime:
    return datetime.now(TIMEZONE)


def _print_report(report: ImportReport) -> None:
    for issue in report.errors:
        print(f"ERRO   {issue.describe()}")
    for issue in report.warnings:
        print(f"AVISO  {issue.describe()}")
    data = report.data
    print(
        f"\n{len(data.properties)} casas, {len(data.rates)} tarifas, {len(data.blocks)} bloqueios, "
        f"{len(data.rules)} regras · {len(report.errors)} erros, {len(report.warnings)} avisos"
    )
    if report.ok:
        print(f"Planilha válida. Hash: {data.content_hash()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.importer")
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="gera a planilha modelo vazia")
    template.add_argument("--out", type=Path, default=Path("planilha-modelo.xlsx"))
    sample = commands.add_parser("sample", help="gera a planilha de exemplo (Demo Riviera)")
    sample.add_argument("--season", type=int, default=_today().year)
    sample.add_argument("--out", type=Path, default=Path("exemplo-demo-riviera.xlsx"))
    check = commands.add_parser("check", help="confere uma planilha preenchida")
    check.add_argument("file", type=Path)
    check.add_argument("--links", action="store_true", help="confere os links de fotos (HTTP)")
    args = parser.parse_args(argv)

    if args.command == "template":
        args.out.write_bytes(build_workbook())
        print(f"Planilha modelo gravada em {args.out}")
        return 0
    if args.command == "sample":
        args.out.write_bytes(build_workbook(build_sample(args.season)))
        print(f"Planilha de exemplo gravada em {args.out}")
        return 0
    checker: LinkChecker = HttpLinkChecker() if args.links else FakeLinkChecker()
    report = asyncio.run(
        prepare_import(args.file.read_bytes(), today=_today().date(), link_checker=checker)
    )
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
