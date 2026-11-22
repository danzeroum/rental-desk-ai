"""Textos fixos enviados ao hóspede pelo código (não passam pelo LLM)."""

from __future__ import annotations

from typing import Final

RATE_LIMIT_NOTICE: Final = (
    "Recebemos muitas mensagens em sequência. Vou aguardar alguns minutos antes de continuar "
    "o atendimento. Obrigado pela paciência!"
)

OPT_OUT_CONFIRMED: Final = (
    "Pronto: este número não vai mais receber mensagens automáticas da nossa central. "
    "Se quiser voltar a ser atendido, escreva VOLTAR."
)
STOP_WORDS: Final = frozenset({"parar", "pare", "stop", "sair", "descadastrar"})
RESUME_WORDS: Final = frozenset({"voltar", "volta", "reativar"})


def _command(text: str) -> str:
    return " ".join(text.lower().replace("!", " ").replace(".", " ").split())


def is_stop(text: str) -> bool:
    """RF21: "PARAR" sozinho na mensagem (não "parar na praia")."""
    return _command(text) in STOP_WORDS


def is_resume(text: str) -> bool:
    return _command(text) in RESUME_WORDS
