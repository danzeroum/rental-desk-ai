"""Livro de eliminações: as exclusões pedidas por titulares, fora do banco.

Um backup restaurado traria de volta quem pediu exclusão depois dele (revisão LGPD de
24/11/2026, Risco-003). Por isso cada exclusão também vai, antes de mexer no banco, para este
arquivo append-only num volume próprio (copiado para o backup e para a cópia externa). Depois
de uma restauração, `python -m app.cli replay-erasures` lê o livro e apaga de novo quem
reapareceu.

Uma linha JSON por exclusão, só com ids internos (imobiliária, contato) e a data: nenhum
telefone ou nome. Se o banco falhar depois de gravar a linha, a próxima reaplicação apaga o
contato, o que era o pedido do titular.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)
FILE_MODE = 0o644
"""Só ids internos: o contêiner de backup (outro usuário) precisa ler para copiar."""


@dataclass(frozen=True)
class Erasure:
    at: datetime
    tenant_id: uuid.UUID
    contact_id: uuid.UUID


class ErasureLedger:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    async def append(self, erasure: Erasure) -> None:
        """Grava e força no disco. Sem caminho configurado (desenvolvimento), não faz nada."""
        if self.path is not None:
            await asyncio.to_thread(_write, self.path, erasure)

    def read(self) -> list[Erasure]:
        """Todas as exclusões; linha corrompida é registrada no log e pulada."""
        if self.path is None or not self.path.exists():
            return []
        return parse(self.path.read_text())

    async def merge(self, entries: list[Erasure]) -> int:
        """Acrescenta as exclusões que faltam (ex.: o livro da cópia externa depois de perder
        a VPS). Devolve quantas entraram."""
        if self.path is None:
            return 0
        known = {(e.tenant_id, e.contact_id) for e in self.read()}
        added = 0
        for entry in entries:
            if (entry.tenant_id, entry.contact_id) not in known:
                await self.append(entry)
                known.add((entry.tenant_id, entry.contact_id))
                added += 1
        return added


def _write(path: Path, erasure: Erasure) -> None:
    line = json.dumps({
        "at": erasure.at.isoformat(),
        "tenant_id": str(erasure.tenant_id),
        "contact_id": str(erasure.contact_id),
    })  # fmt: skip
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
    try:
        os.write(fd, (line + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def parse(content: str) -> list[Erasure]:
    entries: list[Erasure] = []
    for number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            entries.append(Erasure(
                datetime.fromisoformat(raw["at"]), uuid.UUID(raw["tenant_id"]),
                uuid.UUID(raw["contact_id"]),
            ))  # fmt: skip
        except (ValueError, KeyError, TypeError):
            log.warning("livro_eliminacoes_linha_invalida", linha=number)
    return entries
