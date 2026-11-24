"""Mascaramento de dados pessoais em texto livre (SPEC, seção 7, item 8).

Módulo puro, sem I/O. Usado nos logs e, a partir do M5, nos prompts do LLM.
A ordem importa: padrões mais específicos (e-mail, chave aleatória, cartão)
vêm antes dos genéricos (CPF, telefone) para não serem cortados ao meio.
"""

from __future__ import annotations

import re
from typing import Any, Final

_DIGIT_GROUP_SEP = r"[ .\-]?"

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[EMAIL]"),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "[CHAVE_PIX]",
    ),
    # Telefone com DDI colado (formato do WhatsApp) antes do cartão: 12-13 dígitos.
    (re.compile(r"(?<!\d)55\d{2}9?\d{8}(?!\d)"), "[TELEFONE]"),
    (re.compile(r"(?<!\d)(?:\d{4}[ \-]?){3}\d{1,7}(?!\d)"), "[CARTAO]"),
    (re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"), "[CNPJ]"),
    (re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), "[CPF]"),
    (
        re.compile(
            r"(?<![\w/])(?:\+?55" + _DIGIT_GROUP_SEP + r")?"
            r"(?:\(\d{2}\)|\d{2})" + _DIGIT_GROUP_SEP + r"9?\d{4}" + _DIGIT_GROUP_SEP + r"\d{4}"
            r"(?![\w/])"
        ),
        "[TELEFONE]",
    ),
)


def mask_pii(text: str) -> str:
    """Troca CPF, CNPJ, cartão, e-mail, chave Pix aleatória e telefone por marcadores."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_ID_KEYS: Final = re.compile(r"(^id$|_id$|_ids$|_by$|hash|^at$|_at$)")
"""Campos estruturados com identificadores internos e datas: não são dado pessoal. Um UUID
interno tem o mesmo formato de uma chave Pix aleatória; mascará-lo apagaria a ligação entre os
eventos da trilha."""


def mask_structured(value: Any, key: str = "") -> Any:
    """Máscara de dados pessoais num JSON (payload da trilha): só nos textos livres.

    Identificadores e datas (`*_id`, `*_ids`, `*_by`, `*_at`, hashes) ficam como estão.
    """
    if isinstance(value, dict):
        return {k: mask_structured(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_structured(item, key) for item in value]
    if isinstance(value, str) and not _ID_KEYS.search(key):
        return mask_pii(value)
    return value
