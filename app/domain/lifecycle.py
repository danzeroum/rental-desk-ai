"""Ciclo da imobiliária: implantação → modo sombra → atendendo (SPEC seção 11). Puro.

O modo sombra roda sobre as conversas reais e grava a resposta que o assistente daria,
sem enviar; o gestor e o operador comparam e então ativam. Recomendação: 2 dias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

SHADOW_RECOMMENDED: Final = timedelta(days=2)
ONBOARDING, SHADOW, ACTIVE, SUSPENDED = "onboarding", "shadow", "active", "suspended"
TRANSITIONS: Final = {
    (ONBOARDING, SHADOW): "iniciar o modo sombra",
    (SHADOW, ACTIVE): "ativar o atendimento",
    (ACTIVE, SHADOW): "voltar ao modo sombra",
    (SHADOW, ONBOARDING): "voltar para a implantação",
}


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class Readiness:
    has_approved_data: bool
    has_number: bool
    shadow_since: datetime | None
    shadow_responses: int


def check_transition(
    current: str, target: str, ready: Readiness, now: datetime, *, early: bool
) -> None:
    """Levanta `TransitionError` com a mensagem para o painel quando a troca não pode ser feita."""
    if (current, target) not in TRANSITIONS:
        raise TransitionError("Essa mudança de situação não é permitida.")
    if target in {SHADOW, ACTIVE} and not ready.has_approved_data:
        raise TransitionError(
            "Aprove uma planilha antes: sem dados aprovados o assistente não responde."
        )
    if target in {SHADOW, ACTIVE} and not ready.has_number:
        raise TransitionError("Conecte o número de WhatsApp antes: sem ele nenhuma mensagem chega.")
    if (current, target) == (SHADOW, ACTIVE):
        if ready.shadow_responses == 0:
            raise TransitionError("Ainda não há respostas do modo sombra para conferir.")
        too_soon = ready.shadow_since is None or now - ready.shadow_since < SHADOW_RECOMMENDED
        if too_soon and not early:
            raise TransitionError(
                "O modo sombra ainda não completou 2 dias. Para ativar antes, marque a confirmação."
            )
