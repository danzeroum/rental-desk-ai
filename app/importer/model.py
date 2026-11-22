"""Resultado da leitura da planilha: dados normalizados e apontamentos por aba/linha."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.domain.catalog import BlockData, PropertyData, RateData


class Severity(StrEnum):
    ERROR = "error"  # bloqueia a importação
    WARNING = "warning"  # aparece no painel, não bloqueia


@dataclass(frozen=True)
class Issue:
    severity: Severity
    sheet: str
    message: str
    row: int | None = None
    column: str | None = None

    def describe(self) -> str:
        where = self.sheet
        if self.row is not None:
            where += f", linha {self.row}"
        if self.column:
            where += f", coluna {self.column}"
        return f"{where}: {self.message}"


__all__ = ["BlockData", "PropertyData", "RateData"]


@dataclass(frozen=True)
class RawRow:
    """Linha lida da planilha: número da linha no Excel e valores por nome de coluna."""

    number: int
    values: dict[str, object]


@dataclass
class WorkbookData:
    properties: list[PropertyData] = field(default_factory=list)
    rates: list[RateData] = field(default_factory=list)
    blocks: list[BlockData] = field(default_factory=list)
    rules: dict[str, str] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        """Representação estável (ordenada) para o hash do snapshot."""
        return {
            "properties": sorted((asdict(p) for p in self.properties), key=lambda p: p["code"]),
            "rates": sorted(
                (asdict(r) for r in self.rates),
                key=lambda r: (r["property_code"], r["start_date"]),
            ),
            "blocks": sorted(
                (asdict(b) for b in self.blocks),
                key=lambda b: (b["property_code"], b["start_date"], b["end_date"]),
            ),
            "rules": dict(sorted(self.rules.items())),
        }

    def catalog_hash(self) -> str:
        """Hash sem os bloqueios: muda só com casa, tarifa ou regra (RF27: pede aprovação)."""
        canonical = {k: v for k, v in self.canonical().items() if k != "blocks"}
        payload = json.dumps(canonical, default=_json_default, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def content_hash(self) -> str:
        payload = json.dumps(
            self.canonical(), default=_json_default, ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"tipo não serializável: {type(value).__name__}")


@dataclass
class ImportReport:
    data: WorkbookData
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors
