"""Validador do texto livre escrito pelo LLM (SPEC, seção 7, item 3). Puro, sem I/O.

A IA só escreve uma frase de abertura e uma de fechamento, sem fatos. Este
validador recusa tudo que pareça fato ou promessa: dígitos, números por extenso,
datas, valores, códigos de casa, links e as palavras proibidas. Falhou → o
pipeline pede outra frase uma vez → falhou de novo → frase fixa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MAX_PHRASE_CHARS: Final = 220

FORBIDDEN_WORDS: Final = frozenset(
    {
        "confirmado", "confirmada", "confirmados", "confirmadas", "confirmo",
        "garantido", "garantida", "garantidos", "garantidas", "garanto",
        "reservado", "reservada", "reservados", "reservadas",
        "desconto", "descontos", "prometo", "prometemos",
    }
)  # fmt: skip
"""SPEC, seção 7, item 3 (com plurais e flexões óbvias)."""

# "um"/"uma" ficam de fora: são artigos ("uma casa").
NUMBER_WORDS: Final = frozenset(
    {
        "zero", "dois", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
        "onze", "doze", "treze", "catorze", "quatorze", "quinze", "dezesseis", "dezessete",
        "dezoito", "dezenove", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
        "oitenta", "noventa", "cem", "cento", "duzentos", "duzentas", "trezentos", "quatrocentos",
        "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos", "mil", "milhao",
        "milhoes", "bilhao", "dezena", "duzia", "centena",
    }
)  # fmt: skip
MONTHS: Final = (
    "janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
)
_DAY_OF_MONTH: Final = re.compile(rf"\b(primeiro|\w+)\s+de\s+({MONTHS})\b")
_WORD: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_LINK: Final = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|br|net|org)\b)", re.IGNORECASE)
_MONEY: Final = re.compile(r"(r\$|\breais\b|\breal\b|\bcentavos?\b)", re.IGNORECASE)


class Rejection(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    DIGITS = "digits"
    NUMBER_WORDS = "number_words"
    DATE = "date"
    MONEY = "money"
    PROPERTY_CODE = "property_code"
    FORBIDDEN_WORD = "forbidden_word"
    LINK = "link"


@dataclass(frozen=True)
class ValidationResult:
    reasons: tuple[Rejection, ...]

    @property
    def ok(self) -> bool:
        return not self.reasons


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def validate_phrase(text: str, *, property_codes: frozenset[str]) -> ValidationResult:
    """Confere uma frase do LLM. `property_codes`: códigos das casas da imobiliária."""
    if not text.strip():
        return ValidationResult((Rejection.EMPTY,))
    folded = _fold(text)
    words = set(_WORD.findall(folded))
    checks: list[tuple[Rejection, bool]] = [
        (Rejection.TOO_LONG, len(text) > MAX_PHRASE_CHARS),
        (Rejection.DIGITS, any(ch.isdigit() for ch in text)),
        (Rejection.NUMBER_WORDS, bool(words & NUMBER_WORDS)),
        (Rejection.DATE, bool(_DAY_OF_MONTH.search(folded)) and _has_day_word(folded)),
        (Rejection.MONEY, bool(_MONEY.search(folded))),
        (Rejection.PROPERTY_CODE, bool(words & {_fold(code) for code in property_codes})),
        (Rejection.FORBIDDEN_WORD, bool(words & FORBIDDEN_WORDS)),
        (Rejection.LINK, bool(_LINK.search(text))),
    ]
    return ValidationResult(tuple(reason for reason, failed in checks if failed))


def _has_day_word(folded: str) -> bool:
    """ "<dia> de <mês>": dia por extenso, ordinal ou dígito (dígito já é recusado à parte)."""
    for match in _DAY_OF_MONTH.finditer(folded):
        day = match.group(1)
        if day == "primeiro" or day in NUMBER_WORDS or day.isdigit():
            return True
    return False
