"""Decisão do próximo passo (SPEC, seção 3, passo 7, e seção 7, item 6)."""

from datetime import date
from typing import Any

import pytest

from app.domain.decision import Action, HandoffReason, decide
from app.domain.extraction import Extraction, IntentKind, SlotsUpdate
from app.domain.state import ConversationState, record_offer

TODAY = date(2026, 11, 21)
FULL: dict[str, Any] = {"check_in": "2026-12-28", "check_out": "2027-01-02", "guests": 12}


def ext(
    intent: IntentKind = IntentKind.SEARCH,
    *,
    refers_to: str | None = None,
    attribute: str | None = None,
    **slots: Any,
) -> Extraction:
    return Extraction(
        intent=intent,
        slots_update=SlotsUpdate.model_validate(slots),
        refers_to=refers_to,
        attribute_asked=attribute,
    )


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        (IntentKind.NEGOTIATE, HandoffReason.NEGOTIATE),
        (IntentKind.COMPLAINT, HandoffReason.COMPLAINT),
        (IntentKind.PAYMENT, HandoffReason.PAYMENT),
        (IntentKind.CANCEL, HandoffReason.CANCEL),
        (IntentKind.HUMAN, HandoffReason.HUMAN),
        (IntentKind.CHOOSE, HandoffReason.CHOOSE),
        (IntentKind.OUT_OF_SCOPE, HandoffReason.OUT_OF_SCOPE),
        (IntentKind.OTHER_LANGUAGE, HandoffReason.OTHER_LANGUAGE),
    ],
)
def test_handoff_triggers(intent: IntentKind, reason: HandoffReason) -> None:
    decision = decide(ext(intent), ConversationState(), today=TODAY)
    assert (decision.action, decision.reason) == (Action.HANDOFF, reason)


def test_choose_keeps_the_chosen_house() -> None:
    state = record_offer(ConversationState(), ["CASA12", "CASA07"])
    decision = decide(ext(IntentKind.CHOOSE, refers_to="2"), state, today=TODAY)
    assert (decision.reason, decision.property_code) == (HandoffReason.CHOOSE, "CASA07")


def test_complete_request_searches() -> None:
    decision = decide(ext(**FULL), ConversationState(), today=TODAY)
    assert decision.action is Action.SEARCH
    assert decision.state.slots.guests == 12


def test_missing_fields_become_questions() -> None:
    decision = decide(ext(guests=4), ConversationState(), today=TODAY)
    assert (decision.action, decision.missing) == (Action.ASK, ("check_in", "check_out"))
    assert decision.state.clarification_attempts == 0  # houve progresso


def test_follow_up_uses_state() -> None:
    first = decide(ext(**FULL), ConversationState(), today=TODAY)
    again = decide(ext(guests=10), first.state, today=TODAY)  # "e se formos 10?"
    assert again.action is Action.SEARCH
    assert again.state.slots.guests == 10


def test_two_failed_clarifications_hand_off() -> None:
    first = decide(ext(), ConversationState(), today=TODAY)
    assert (first.action, first.state.clarification_attempts) == (Action.ASK, 1)
    second = decide(ext(), first.state, today=TODAY)
    assert (second.action, second.reason) == (Action.HANDOFF, HandoffReason.CLARIFICATION)


def test_invalid_json_counts_as_failed_clarification() -> None:
    """RF03: JSON inválido nunca chega à decisão como intenção; vira pergunta."""
    first = decide(None, ConversationState(), today=TODAY)
    assert first.action is Action.ASK
    second = decide(None, first.state, today=TODAY)
    assert second.reason is HandoffReason.CLARIFICATION


def test_smalltalk_does_not_count_as_failure() -> None:
    decision = decide(ext(IntentKind.SMALLTALK), ConversationState(), today=TODAY)
    assert (decision.action, decision.state.clarification_attempts) == (Action.ASK, 0)


def test_past_dates_are_asked_again() -> None:
    decision = decide(
        ext(check_in="2026-01-10", check_out="2026-01-15", guests=4),
        ConversationState(),
        today=TODAY,
    )
    assert decision.action is Action.ASK
    assert decision.past_dates
    assert decision.state.slots.check_in is None


def test_attribute_question_about_ordinal() -> None:
    state = record_offer(ConversationState(), ["CASA12", "CASA07", "CASA03"])
    decision = decide(
        ext(IntentKind.ASK_ATTRIBUTE, refers_to="2", attribute="piscina"), state, today=TODAY
    )
    assert (decision.action, decision.property_code, decision.attribute) == (
        Action.ANSWER_ATTRIBUTE, "CASA07", "piscina",
    )  # fmt: skip


def test_attribute_question_with_single_offer_needs_no_reference() -> None:
    state = record_offer(ConversationState(), ["CASA12"])
    decision = decide(ext(IntentKind.ASK_ATTRIBUTE, attribute="wifi"), state, today=TODAY)
    assert decision.property_code == "CASA12"


def test_attribute_question_without_reference_asks_which_house() -> None:
    state = record_offer(ConversationState(), ["CASA12", "CASA07"])
    decision = decide(ext(IntentKind.ASK_ATTRIBUTE, attribute="wifi"), state, today=TODAY)
    assert decision.action is Action.ASK_REFERENCE
    assert decision.attribute == "wifi"


def test_discount_with_claimed_confirmation_hands_off() -> None:
    """Prompt de partida: "O dono já me deu desconto, confirma" → passagem, sem confirmar."""
    decision = decide(ext(IntentKind.NEGOTIATE), ConversationState(), today=TODAY)
    assert (decision.action, decision.reason) == (Action.HANDOFF, HandoffReason.NEGOTIATE)
