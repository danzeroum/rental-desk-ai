"""Conversão de células da planilha em valores do domínio. Puro, sem I/O.

Cada função devolve `None` para célula vazia e levanta `CellError` com uma
mensagem para o gestor quando o conteúdo não serve.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from urllib.parse import urlsplit

from app.importer.schema import FALSE_WORDS, TRUE_WORDS, normalize_name

CODE_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")
_BR_DATE: Final = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_THOUSANDS_DOT: Final = re.compile(r"^\d{1,3}(\.\d{3})+$")
_CENT: Final = Decimal("0.01")
_TRUE: Final = frozenset(normalize_name(word) for word in TRUE_WORDS)
_FALSE: Final = frozenset(normalize_name(word) for word in FALSE_WORDS)


class CellError(ValueError):
    """Conteúdo de célula inválido; a mensagem é mostrada ao gestor."""


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_code(value: object) -> str | None:
    if _is_empty(value):
        return None
    if isinstance(value, bool | float):
        raise CellError("código inválido: use letras e números, ex.: CASA12")
    code = str(value).strip().upper()
    if not CODE_PATTERN.match(code):
        raise CellError(
            f"código inválido '{code[:30]}': use até 20 letras, números, - ou _, sem espaços"
        )
    return code


def parse_text(value: object, max_length: int) -> str | None:
    if _is_empty(value):
        return None
    text = str(value).strip()
    if len(text) > max_length:
        raise CellError(f"texto longo demais: no máximo {max_length} caracteres")
    return text


def parse_url(value: object) -> str | None:
    text = parse_text(value, 500)
    if text is None:
        return None
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise CellError("link inválido: precisa começar com https:// (ou http://)")
    return text


def parse_int(value: object, *, min_value: int) -> int | None:
    if _is_empty(value):
        return None
    number: int
    if isinstance(value, bool):
        raise CellError("use um número inteiro")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise CellError("use um número inteiro")
    if number < min_value:
        raise CellError(f"o número precisa ser pelo menos {min_value}")
    return number


def parse_bool(value: object) -> bool | None:
    if _is_empty(value):
        return None
    if isinstance(value, bool):
        return value
    word = normalize_name(str(value))
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    raise CellError("use Sim ou Não (ou deixe vazio se não souber)")


def parse_date(value: object) -> date | None:
    if _is_empty(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            if match := _BR_DATE.match(text):
                day, month, year = (int(g) for g in match.groups())
                return date(year, month, day)
            if match := _ISO_DATE.match(text):
                year, month, day = (int(g) for g in match.groups())
                return date(year, month, day)
        except ValueError:
            raise CellError(f"data inexistente: {text}") from None
    raise CellError("use uma data no formato dd/mm/aaaa")


def _decimal_from_text(text: str) -> Decimal:
    cleaned = text.replace("R$", "").replace(" ", "").strip()
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif _THOUSANDS_DOT.match(cleaned):
        cleaned = cleaned.replace(".", "")
    return Decimal(cleaned)


def parse_money_cents(value: object) -> int | None:
    """Reais → centavos (inteiro). Aceita 1500, 1500,50, 1.500,00 e R$ 1.500."""
    if _is_empty(value):
        return None
    if isinstance(value, bool):
        raise CellError("valor inválido: use só números, ex.: 1500,00")
    try:
        if isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            amount = Decimal(repr(value))
        else:
            amount = _decimal_from_text(str(value))
    except InvalidOperation:
        raise CellError("valor inválido: use só números, ex.: 1500,00") from None
    if not amount.is_finite():
        raise CellError("valor inválido: use só números, ex.: 1500,00")
    if amount < 0:
        raise CellError("valor negativo não é permitido")
    if amount != amount.quantize(_CENT):
        raise CellError("valor com mais de duas casas de centavos")
    return int(amount * 100)
