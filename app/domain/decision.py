"""Decisão do próximo passo (SPEC, seção 3, passo 7). Puro, sem I/O.

perguntar o que falta | buscar | responder atributo | passar para humano.
A IA só fornece a extração; quem decide é este código.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from app.domain.extraction import Extraction, IntentKind
from app.domain.state import (
    ConversationState,
    merge_extraction,
    missing_for_search,
    resolve_reference,
)

MAX_CLARIFICATION_ATTEMPTS: Final = 2


class Action(StrEnum):
    ASK = "ask"
    SEARCH = "search"
    ANSWER_ATTRIBUTE = "answer_attribute"
    ASK_REFERENCE = "ask_reference"
    HANDOFF = "handoff"


class HandoffReason(StrEnum):
    NEGOTIATE = "negotiate"
    COMPLAINT = "complaint"
    PAYMENT = "payment"
    CANCEL = "cancel"
    HUMAN = "human"
    CHOOSE = "choose"
    OUT_OF_SCOPE = "out_of_scope"
    OTHER_LANGUAGE = "other_language"
    CLARIFICATION = "clarification_attempts"
    LLM_UNAVAILABLE = "llm_unavailable"


# SPEC, seção 7, item 6.
HANDOFF_INTENTS: Final = {
    IntentKind.NEGOTIATE: HandoffReason.NEGOTIATE,
    IntentKind.COMPLAINT: HandoffReason.COMPLAINT,
    IntentKind.PAYMENT: HandoffReason.PAYMENT,
    IntentKind.CANCEL: HandoffReason.CANCEL,
    IntentKind.HUMAN: HandoffReason.HUMAN,
    IntentKind.CHOOSE: HandoffReason.CHOOSE,
    IntentKind.OUT_OF_SCOPE: HandoffReason.OUT_OF_SCOPE,
    IntentKind.OTHER_LANGUAGE: HandoffReason.OTHER_LANGUAGE,
}


@dataclass(frozen=True)
class Decision:
    action: Action
    state: ConversationState
    reason: HandoffReason | None = None
    missing: tuple[str, ...] = ()
    property_code: str | None = None
    attribute: str | None = None
    past_dates: bool = False


def _with_attempts(state: ConversationState, attempts: int) -> ConversationState:
    return state.model_copy(update={"clarification_attempts": attempts})


def _failed_clarification(state: ConversationState, missing: tuple[str, ...]) -> Decision:
    attempts = state.clarification_attempts + 1
    updated = _with_attempts(state, attempts)
    if attempts >= MAX_CLARIFICATION_ATTEMPTS:
        return Decision(Action.HANDOFF, updated, reason=HandoffReason.CLARIFICATION)
    return Decision(Action.ASK, updated, missing=missing)


def _drop_past_dates(state: ConversationState, today: date) -> tuple[ConversationState, bool]:
    check_in = state.slots.check_in
    if check_in is None or check_in >= today:
        return state, False
    slots = state.slots.model_copy(update={"check_in": None, "check_out": None})
    return state.model_copy(update={"slots": slots}), True


def _decide_attribute(extraction: Extraction, merged: ConversationState) -> Decision:
    code = resolve_reference(merged, extraction.refers_to)
    if code is None and len(merged.last_offered) == 1:
        code = merged.last_offered[0]
    if code is not None:
        return Decision(
            Action.ANSWER_ATTRIBUTE,
            _with_attempts(merged, 0),
            property_code=code,
            attribute=extraction.attribute_asked,
        )
    failed = _failed_clarification(merged, ())
    if failed.action is Action.HANDOFF:
        return failed
    return Decision(Action.ASK_REFERENCE, failed.state, attribute=extraction.attribute_asked)


def _decide_search(
    extraction: Extraction, merged: ConversationState, previous: ConversationState
) -> Decision:
    missing = tuple(missing_for_search(merged))
    if not missing:
        return Decision(Action.SEARCH, _with_attempts(merged, 0))
    progressed = merged.slots != previous.slots
    if extraction.intent is IntentKind.SMALLTALK or progressed:
        return Decision(Action.ASK, _with_attempts(merged, 0), missing=missing)
    return _failed_clarification(merged, missing)


def decide(extraction: Extraction | None, previous: ConversationState, *, today: date) -> Decision:
    """`extraction=None`: a saída do LLM não passou no esquema (RF03)."""
    if extraction is None:
        return _failed_clarification(previous, tuple(missing_for_search(previous)))
    merged = merge_extraction(previous, extraction)
    reason = HANDOFF_INTENTS.get(extraction.intent)
    if reason is not None:
        code = resolve_reference(merged, extraction.refers_to)
        return Decision(Action.HANDOFF, merged, reason=reason, property_code=code)
    merged, past = _drop_past_dates(merged, today)
    if past:
        return Decision(
            Action.ASK, merged, missing=tuple(missing_for_search(merged)), past_dates=True
        )
    if extraction.intent is IntentKind.ASK_ATTRIBUTE:
        return _decide_attribute(extraction, merged)
    return _decide_search(extraction, merged, previous)
