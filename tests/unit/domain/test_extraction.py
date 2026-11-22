from datetime import date

import pytest

from app.domain.extraction import IntentKind, parse_extraction
from app.domain.search import Attribute


def test_valid_full_extraction() -> None:
    result = parse_extraction(
        '{"intent": "search", "slots_update": {"check_in": "2026-12-28", "check_out": "2027-01-02",'
        ' "guests": 12, "budget_max_brl": 40000, "neighborhood": null, "required": ["piscina"]},'
        ' "refers_to": null, "attribute_asked": null}'
    )
    assert result is not None
    assert result.intent is IntentKind.SEARCH
    slots = result.slots_update
    assert (slots.check_in, slots.check_out, slots.guests) == (
        date(2026, 12, 28),
        date(2027, 1, 2),
        12,
    )
    assert slots.budget_max_brl == 40000
    assert slots.required == [Attribute.PISCINA]


def test_minimal_extraction() -> None:
    result = parse_extraction({"intent": "smalltalk"})
    assert result is not None
    assert result.slots_update.guests is None


@pytest.mark.parametrize(
    "raw",
    [
        "não é json",
        '{"intent": "reservar"}',  # intenção fora da lista
        '{"slots_update": {}}',  # sem intenção
        '{"intent": "search", "extra": 1}',  # campo fora do esquema
        '{"intent": "search", "slots_update": {"preco": 10}}',
        "[]",
    ],
)
def test_invalid_json_never_passes(raw: str) -> None:
    assert parse_extraction(raw) is None


@pytest.mark.parametrize(
    ("slots", "field"),
    [
        ({"check_in": "2027-02-30"}, "check_in"),
        ({"guests": 0}, "guests"),
        ({"guests": 500}, "guests"),
        ({"budget_max_brl": -5}, "budget_max_brl"),
        ({"check_out": "amanhã"}, "check_out"),
    ],
)
def test_impossible_value_becomes_unknown_without_dropping_the_rest(
    slots: dict[str, object], field: str
) -> None:
    result = parse_extraction(
        {"intent": "search", "slots_update": {**slots, "neighborhood": "Módulo 5"}}
    )
    assert result is not None
    assert getattr(result.slots_update, field) is None
    assert result.slots_update.neighborhood == "Módulo 5"


def test_unknown_attributes_are_dropped() -> None:
    result = parse_extraction(
        {"intent": "search", "slots_update": {"required": ["piscina", "heliponto", 3]}}
    )
    assert result is not None
    assert result.slots_update.required == [Attribute.PISCINA]


@pytest.mark.parametrize(
    ("refers_to", "expected"),
    [("2", "2"), ("casa12", "CASA12"), ("4", "4"), ("a segunda", None), ("CASA 12", None)],
)
def test_refers_to_is_code_or_ordinal(refers_to: str, expected: str | None) -> None:
    result = parse_extraction({"intent": "ask_attribute", "refers_to": refers_to})
    assert result is not None
    assert result.refers_to == expected
