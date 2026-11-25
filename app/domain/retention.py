"""Retenção dos dados do hóspede (SPEC, seção 9; política de privacidade). Puro, sem I/O.

Uma conversa parada há mais de `days` dias perde nome e telefone do hóspede. A regra é
"mais de": no dia exato do prazo ainda não vence. Uma passagem ao corretor ainda aberta
(ou assumida e não encerrada) segura o contato: é a "disputa aberta" do SPEC (D048).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

ANONYMIZED_MARK = "[anonimizado]"


def cutoff(now: datetime, days: int) -> datetime:
    """Última atividade anterior a este instante vence."""
    return now - timedelta(days=days)


def expired(last_activity: datetime, now: datetime, days: int) -> bool:
    return last_activity < cutoff(now, days)


def scrub(text: str | None, phone: str | None, name: str | None) -> str | None:
    """Tira do texto o telefone e o nome do perfil do hóspede (o resto passa por `mask_pii`
    em quem chama). Nome curto demais (1-2 letras) trocaria pedaços de outras palavras."""
    if text is None:
        return None
    for needle in (phone, phone.removeprefix("+") if phone else None, name):
        if needle and len(needle) >= 3:  # noqa: PLR2004
            text = text.replace(needle, ANONYMIZED_MARK)
    return text


@dataclass(frozen=True)
class RetentionResult:
    contacts: int = 0
    messages: int = 0
