"""Estado da conversa (`conversation.state_json`, SPEC seção 6) e mescla da extração.

A mescla é feita pelo código, nunca pela IA (SPEC, seção 3, passo 6). Funções
puras: recebem um estado e devolvem outro, sem alterar o original.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.extraction import Extraction
from app.domain.pricing import InvalidStayError, Stay
from app.domain.search import Attribute

SEARCH_SLOTS: Final = ("check_in", "check_out", "guests")
MAX_PENDING_QUESTIONS: Final = 10


class Slots(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    check_in: date | None = None
    check_out: date | None = None
    guests: int | None = Field(default=None, ge=1)
    budget_max_cents: int | None = Field(default=None, ge=0)
    neighborhood: str | None = None
    required: list[Attribute] = Field(default_factory=list)
    pets: bool | None = None


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    slots: Slots = Field(default_factory=Slots)
    last_offered: list[str] = Field(default_factory=list, max_length=3)
    pending_questions: list[str] = Field(default_factory=list, max_length=MAX_PENDING_QUESTIONS)
    clarification_attempts: int = Field(default=0, ge=0)
    language: str = "pt"

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(mode="json")
        return data


def load_state(raw: dict[str, Any] | None) -> ConversationState:
    """Estado salvo → objeto; ausente ou corrompido vira o estado inicial."""
    if not raw:
        return ConversationState()
    try:
        return ConversationState.model_validate(raw)
    except ValidationError:
        return ConversationState()


def _merge_required(current: list[Attribute], new: list[Attribute]) -> list[Attribute]:
    return list(dict.fromkeys([*current, *new]))


def merge_extraction(state: ConversationState, extraction: Extraction) -> ConversationState:
    """Aplica os campos informados; mantém os demais (perguntas de acompanhamento)."""
    update = extraction.slots_update
    slots = state.slots
    check_in = update.check_in or slots.check_in
    check_out = update.check_out or slots.check_out
    if check_in and check_out and check_out <= check_in:
        # Par impossível (entrada nova depois da saída antiga, ou saída nova antes da entrada):
        # a saída deixa de valer e volta a ser perguntada.
        check_out = None
    required = _merge_required(slots.required, list(update.required))
    new_slots = slots.model_copy(
        update={
            "check_in": check_in,
            "check_out": check_out,
            "guests": update.guests or slots.guests,
            "budget_max_cents": (
                update.budget_max_brl * 100
                if update.budget_max_brl is not None
                else slots.budget_max_cents
            ),
            "neighborhood": update.neighborhood or slots.neighborhood,
            "required": required,
            "pets": True if Attribute.PET in required else slots.pets,
        }
    )
    return state.model_copy(update={"slots": new_slots})


def missing_for_search(state: ConversationState) -> list[str]:
    """Campos que faltam para buscar, na ordem em que serão perguntados."""
    return [name for name in SEARCH_SLOTS if getattr(state.slots, name) is None]


def stay_from(state: ConversationState) -> Stay | None:
    if state.slots.check_in is None or state.slots.check_out is None:
        return None
    try:
        return Stay(state.slots.check_in, state.slots.check_out)
    except InvalidStayError:
        return None


def record_offer(state: ConversationState, codes: list[str]) -> ConversationState:
    return state.model_copy(update={"last_offered": list(codes[:3])})


def resolve_reference(state: ConversationState, refers_to: str | None) -> str | None:
    """Ordinal ("2") ou código → código entre as últimas casas oferecidas."""
    if refers_to is None:
        return None
    if refers_to.isdigit():
        index = int(refers_to) - 1
        return state.last_offered[index] if 0 <= index < len(state.last_offered) else None
    return refers_to if refers_to in state.last_offered else None
