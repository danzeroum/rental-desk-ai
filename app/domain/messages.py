"""Textos fixos enviados ao hóspede pelo código (não passam pelo LLM)."""

from __future__ import annotations

from typing import Final

RATE_LIMIT_NOTICE: Final = (
    "Recebemos muitas mensagens em sequência. Vou aguardar alguns minutos antes de continuar "
    "o atendimento. Obrigado pela paciência!"
)
