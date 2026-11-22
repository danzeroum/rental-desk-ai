"""Cards e textos por template (RF08, RF09, RF13)."""

from dataclasses import replace
from datetime import date

import pytest

from app.domain.catalog import PropertyData
from app.domain.pricing import Flag, PackageInfo, Quote
from app.domain.render import (
    ATTRIBUTE_UNKNOWN,
    LEGAL_NOTICE,
    answer_attribute,
    br_date,
    brl,
    intro,
    question_for,
    render_offers,
)
from app.domain.search import Offer, SearchOutcome

PROP = PropertyData(
    code="CASA12", title="Casa Azul", neighborhood="Módulo 5", photos_url="https://f/casa12",
    max_guests=12, bedrooms=5, suites=3, bathrooms=4, parking_spots=2, has_pool=True,
    accepts_pets=None, allows_parties=False, ac_in_bedrooms=True, has_wifi=True,
    has_barbecue=True, beach_distance_m=150, public_notes=None,
)  # fmt: skip
IN, OUT = date(2026, 12, 28), date(2027, 1, 2)
PKG = PackageInfo("Réveillon", IN, OUT, 1_500_000)


def offer(
    *,
    flags: frozenset[Flag] = frozenset(),
    package: PackageInfo | None = None,
    confirmed: bool = True,
    over_budget: bool = False,
    min_nights: int | None = None,
) -> Offer:
    quote = Quote("CASA12", 5, 1_530_000, 30_000, flags, min_nights, package)
    return Offer(PROP, quote, over_budget=over_budget, availability_confirmed=confirmed)


def outcome(*offers: Offer, stale: bool = False, over: bool = False) -> SearchOutcome:
    return SearchOutcome(tuple(offers), data_stale=stale, over_budget=over)


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "R$ 0,00"), (5, "R$ 0,05"), (150_050, "R$ 1.500,50"), (123_456_789, "R$ 1.234.567,89")],
)
def test_brl(cents: int, text: str) -> None:
    assert brl(cents) == text


def test_br_date() -> None:
    assert br_date(date(2027, 1, 2)) == "02/01/2027"


def test_card_has_code_title_capacity_value_photos_and_notice() -> None:
    text = render_offers(outcome(offer()), IN, OUT)
    assert "1) *Casa Azul* (CASA12)" in text
    assert "Até 12 pessoas · Módulo 5" in text
    assert "Estimativa: R$ 15.300,00 (5 noites, 28/12/2026 a 02/01/2027)" in text
    assert "Fotos: https://f/casa12" in text
    assert text.endswith(LEGAL_NOTICE)
    assert "a confirmar" not in text


def test_full_package_card() -> None:
    text = render_offers(outcome(offer(package=PKG)), IN, OUT)
    assert "com o pacote Réveillon" in text


def test_requires_full_package_card_shows_package_dates_and_value() -> None:
    text = render_offers(
        outcome(offer(flags=frozenset({Flag.REQUIRES_FULL_PACKAGE}), package=PKG)), IN, OUT
    )
    assert "só aluga o pacote Réveillon completo: 28/12/2026 a 02/01/2027, R$ 15.000,00" in text


def test_below_min_nights() -> None:
    text = render_offers(
        outcome(offer(flags=frozenset({Flag.BELOW_MIN_NIGHTS}), min_nights=5)), IN, OUT
    )
    assert "Mínimo de 5 noites neste período." in text


def test_stale_data_never_claims_availability() -> None:
    text = render_offers(outcome(offer(confirmed=False), stale=True), IN, OUT)
    assert "Disponibilidade a confirmar com o corretor." in text
    assert "ainda não foi atualizado hoje" in text
    for claim in ("disponível", "livre", "está vaga"):
        assert claim not in text.lower()


def test_over_budget_label() -> None:
    text = render_offers(outcome(offer(over_budget=True), over=True), IN, OUT)
    assert "Acima do orçamento informado." in text
    assert "mais próximas" in text


def test_numbered_cards() -> None:
    other = replace(offer(), property=replace(PROP, code="CASA07", title="Casa Verde"))
    text = render_offers(outcome(offer(), other), IN, OUT)
    assert "1) *Casa Azul*" in text and "2) *Casa Verde*" in text
    assert text.count(LEGAL_NOTICE) == 1


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("piscina", "Casa Azul (CASA12): piscina, sim."),
        ("festa", "Casa Azul (CASA12): permite festas, não."),
        ("Wi-Fi", "Casa Azul (CASA12): wi-fi, sim."),
        ("suítes", "Casa Azul (CASA12) tem 3 suítes."),
        ("praia", "Casa Azul (CASA12) fica a 150 metros da praia."),
        ("capacidade", "Casa Azul (CASA12) recebe até 12 pessoas."),
    ],
)
def test_attribute_answers_come_from_data(asked: str, expected: str) -> None:
    assert answer_attribute(PROP, asked) == expected


@pytest.mark.parametrize("asked", ["pet", "carregador de carro elétrico", "piscina aquecida", None])
def test_unknown_attribute_has_no_answer(asked: str | None) -> None:
    """RF09: vazio ou desconhecido → "vou confirmar com o corretor"."""
    assert answer_attribute(PROP, asked) is None
    assert "confirmar com o corretor" in ATTRIBUTE_UNKNOWN


@pytest.mark.parametrize(
    ("missing", "text"),
    [
        (
            ["check_in", "check_out", "guests"],
            "Para quais datas? Me diga o dia de entrada e o de saída (ex.: 28/12 a 02/01). "
            "E para quantas pessoas?",
        ),
        (["check_out"], "Qual é o dia de saída?"),
        (["guests"], "Para quantas pessoas, contando as crianças?"),
    ],
)
def test_questions(missing: list[str], text: str) -> None:
    assert question_for(missing) == text


def test_intro_identifies_as_virtual_assistant_with_privacy_link() -> None:
    text = intro("Lia", "Imobiliária Riviera", "https://app/privacidade/riviera/v1")
    assert "assistente virtual da Imobiliária Riviera" in text
    assert "https://app/privacidade/riviera/v1" in text


def test_intro_with_default_name_is_not_redundant() -> None:
    text = intro("Assistente virtual", "Imobiliária Riviera", "https://x/p")
    assert text.startswith("Olá! Sou o assistente virtual da Imobiliária Riviera.")
