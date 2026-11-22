"""Telefone digitado no painel → E.164 (`+5511988887777`). Puro, sem I/O."""

from __future__ import annotations

import re
from typing import Final

BRAZIL: Final = "55"
_LOCAL_LENGTHS: Final = (10, 11)  # DDD + 8 ou 9 dígitos
_MIN_E164, _MAX_E164 = 8, 15


class InvalidPhoneError(ValueError):
    pass


def to_e164(raw: str) -> str:
    """Aceita "(11) 98888-7777", "11988887777", "+55 11 98888-7777" e estrangeiros com "+"."""
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        if not _MIN_E164 <= len(digits) <= _MAX_E164:
            raise InvalidPhoneError("número internacional inválido")
        return f"+{digits}"
    if len(digits) in _LOCAL_LENGTHS:
        digits = BRAZIL + digits
    if digits.startswith(BRAZIL) and len(digits) - len(BRAZIL) in _LOCAL_LENGTHS:
        if digits[2] == "0":
            raise InvalidPhoneError("DDD inválido")
        return f"+{digits}"
    raise InvalidPhoneError("use DDD + número, por exemplo (11) 98888-7777")
