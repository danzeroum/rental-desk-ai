"""Erros de envio da Meta (WhatsApp Cloud API) em português, com o que fazer (UX-1). Puro.

O status `failed` traz um código e um título em inglês. No rastreio e na Saúde mostramos o que
ele quer dizer e o próximo passo; o texto original da Meta continua gravado na trilha e no CSV.
Códigos desconhecidos aparecem como vieram.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class MetaError:
    meaning: str
    action: str


META_ERRORS: Final = {
    130497: MetaError(
        "a Meta bloqueou envios desta conta para o país do hóspede",
        "conclua a verificação da empresa no Gerenciador de Negócios; se continuar, abra chamado "
        "na Meta (runbook, Rastreio)",
    ),
    131047: MetaError(
        "passaram mais de 24 h desde a última mensagem do hóspede; só vale modelo aprovado",
        "responda pelo WhatsApp do corretor ou espere o hóspede escrever de novo",
    ),
    131026: MetaError(
        "o número não recebeu: sem WhatsApp, aplicativo desatualizado ou bloqueou a conta",
        "confirme o número com o hóspede por outro canal",
    ),
    131049: MetaError(
        "a Meta segurou a mensagem para limitar mensagens a este hóspede",
        "não reenvie em seguida; tente mais tarde ou peça ao corretor",
    ),
    131056: MetaError(
        "mensagens demais para o mesmo número em pouco tempo",
        "espere alguns minutos antes de mandar de novo",
    ),
    131048: MetaError(
        "limite de envio da conta atingido (muitas mensagens marcadas como indesejadas)",
        "reduza mensagens ativas e confira a qualidade do número",
    ),
    131031: MetaError(
        "a conta do WhatsApp Business está bloqueada ou restrita pela Meta",
        "veja o aviso no WhatsApp Manager e peça análise",
    ),
    368: MetaError(
        "a conta foi bloqueada temporariamente por violar políticas da Meta",
        "veja o aviso no WhatsApp Manager; o envio volta ao fim do bloqueio",
    ),
    190: MetaError(
        "o token de acesso da Meta é inválido ou venceu",
        "gere um token novo e rode scripts/numero-teste-meta.sh ou reconecte em Configurações",
    ),
}


def explain(code: int | None, meta_title: str | None = None) -> str:
    """Frase para o painel: o que aconteceu e o que fazer."""
    known = META_ERRORS.get(code) if code is not None else None
    if known is None:
        detail = meta_title or "sem motivo informado"
        return f"código {code} da Meta ({detail})" if code is not None else detail
    return f"código {code}: {known.meaning}. O que fazer: {known.action}"


def short(code: int | None) -> str:
    """Versão curta, para a lista de alertas da Saúde."""
    known = META_ERRORS.get(code) if code is not None else None
    if code is None:
        return ""
    return f"código {code} da Meta: {known.meaning}" if known else f"código {code} da Meta"
