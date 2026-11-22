"""Assistente de implantação (RF28): os 7 passos e quanto já foi feito. Puro.

O progresso é calculado do estado real (número conectado, versões da planilha, uso do
simulador, situação da imobiliária), então fica salvo sem tabela própria e o operador vê
o mesmo que o gestor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.domain.lifecycle import ACTIVE, SHADOW, SHADOW_RECOMMENDED

SIMULATOR_MINIMUM: Final = 3


@dataclass(frozen=True)
class OnboardingFacts:
    has_number: bool
    has_saved_version: bool
    has_sheet_link: bool
    has_approved: bool
    simulator_turns: int
    status: str
    shadow_since: datetime | None


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    hint: str
    link: str
    done: bool


def steps(f: OnboardingFacts, now: datetime) -> list[Step]:
    shadow_done = f.status == ACTIVE or (
        f.status == SHADOW
        and f.shadow_since is not None
        and now - f.shadow_since >= SHADOW_RECOMMENDED
    )
    spec = [
        (
            "Conectar o WhatsApp",
            "o número que a imobiliária já usa, pelo parceiro oficial",
            "#whatsapp",
            f.has_number,
        ),
        (
            "Enviar ou ligar a planilha",
            "planilha modelo pelo painel ou Google; calendários iCal opcionais",
            "/painel/dados",
            f.has_saved_version or f.has_sheet_link,
        ),
        (
            "Corrigir os erros",
            "uma versão só é guardada quando não há erro bloqueante",
            "/painel/dados",
            f.has_saved_version,
        ),
        (
            "Conversar com o assistente",
            f"pelo menos {SIMULATOR_MINIMUM} mensagens no simulador, como um hóspede",
            "/painel/simulador",
            f.simulator_turns >= SIMULATOR_MINIMUM,
        ),
        (
            "Aprovar a planilha",
            "nada vai ao ar sem a sua aprovação",
            "/painel/dados",
            f.has_approved,
        ),
        (
            "Modo sombra por 2 dias",
            "o assistente registra o que responderia, sem enviar",
            "/painel/sombra",
            shadow_done,
        ),
        (
            "Ativar o atendimento",
            "a partir daqui o assistente responde aos hóspedes",
            "/painel/sombra",
            f.status == ACTIVE,
        ),
    ]
    return [Step(i, t, h, link, done) for i, (t, h, link, done) in enumerate(spec, start=1)]


def current(all_steps: list[Step]) -> Step | None:
    """Primeiro passo ainda não feito; `None` quando a imobiliária já está atendendo (último
    passo), mesmo que tenha pulado um passo opcional como o simulador."""
    if all_steps and all_steps[-1].done:
        return None
    return next((s for s in all_steps if not s.done), None)
