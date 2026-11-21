"""Estado da conversa (`conversation.state_json`) e mescla da extração (RF04)."""

from datetime import date

from app.domain.extraction import Extraction, IntentKind, SlotsUpdate
from app.domain.pricing import Stay
from app.domain.search import Attribute
from app.domain.state import (
    ConversationState,
    load_state,
    merge_extraction,
    missing_for_search,
    record_offer,
    resolve_reference,
    stay_from,
)

INITIAL_JSON = {
    "slots": {
        "check_in": None, "check_out": None, "guests": None, "budget_max_cents": None,
        "neighborhood": None, "required": [], "pets": None,
    },
    "last_offered": [], "pending_questions": [], "clarification_attempts": 0, "language": "pt",
}  # fmt: skip


def extraction(**slots: object) -> Extraction:
    return Extraction(intent=IntentKind.SEARCH, slots_update=SlotsUpdate.model_validate(slots))


def test_initial_json_from_spec_round_trips() -> None:
    state = load_state(INITIAL_JSON)
    assert state.to_json() == INITIAL_JSON


def test_missing_or_corrupt_json_becomes_initial_state() -> None:
    assert load_state(None) == ConversationState()
    assert load_state({"slots": {"guests": "muitos"}}) == ConversationState()


def test_merge_keeps_previous_values_and_applies_new_ones() -> None:
    state = merge_extraction(
        ConversationState(), extraction(check_in="2026-12-28", check_out="2027-01-02", guests=12)
    )
    state = merge_extraction(state, extraction(guests=10))  # "e se formos 10?"
    assert (state.slots.check_in, state.slots.check_out, state.slots.guests) == (
        date(2026, 12, 28), date(2027, 1, 2), 10,
    )  # fmt: skip


def test_budget_in_reais_is_stored_in_cents() -> None:
    state = merge_extraction(ConversationState(), extraction(budget_max_brl=40000))
    assert state.slots.budget_max_cents == 4_000_000


def test_required_attributes_accumulate_without_duplicates() -> None:
    state = merge_extraction(ConversationState(), extraction(required=["piscina"]))
    state = merge_extraction(state, extraction(required=["pet", "piscina"]))
    assert state.slots.required == [Attribute.PISCINA, Attribute.PET]
    assert state.slots.pets is True


def test_new_check_in_after_old_check_out_clears_check_out() -> None:
    state = merge_extraction(
        ConversationState(), extraction(check_in="2026-12-20", check_out="2026-12-23")
    )
    state = merge_extraction(state, extraction(check_in="2026-12-27"))
    assert state.slots.check_in == date(2026, 12, 27)
    assert state.slots.check_out is None  # volta a perguntar a saída


def test_check_out_not_after_check_in_is_discarded() -> None:
    state = merge_extraction(ConversationState(), extraction(check_in="2026-12-28"))
    state = merge_extraction(state, extraction(check_out="2026-12-28"))
    assert state.slots.check_out is None


def test_merge_does_not_mutate_input() -> None:
    original = ConversationState()
    merge_extraction(original, extraction(guests=4))
    assert original.slots.guests is None


def test_missing_for_search_in_order() -> None:
    assert missing_for_search(ConversationState()) == ["check_in", "check_out", "guests"]
    state = merge_extraction(ConversationState(), extraction(guests=4, check_out="2026-12-30"))
    assert missing_for_search(state) == ["check_in"]


def test_stay_from_state() -> None:
    assert stay_from(ConversationState()) is None
    state = merge_extraction(
        ConversationState(), extraction(check_in="2026-12-28", check_out="2027-01-02")
    )
    assert stay_from(state) == Stay(date(2026, 12, 28), date(2027, 1, 2))


class TestReferences:
    """ "a segunda tem piscina aquecida?" → casa oferecida em 2º lugar."""

    state = record_offer(ConversationState(), ["CASA12", "CASA07", "CASA03"])

    def test_ordinal(self) -> None:
        assert resolve_reference(self.state, "2") == "CASA07"

    def test_code_among_offered(self) -> None:
        assert resolve_reference(self.state, "CASA03") == "CASA03"

    def test_code_not_offered_is_unknown(self) -> None:
        assert resolve_reference(self.state, "CASA99") is None

    def test_ordinal_beyond_offered(self) -> None:
        short = record_offer(ConversationState(), ["CASA12"])
        assert resolve_reference(short, "3") is None

    def test_none(self) -> None:
        assert resolve_reference(self.state, None) is None

    def test_new_offer_replaces_last_offered(self) -> None:
        state = record_offer(self.state, ["CASA01"])
        assert state.last_offered == ["CASA01"]
