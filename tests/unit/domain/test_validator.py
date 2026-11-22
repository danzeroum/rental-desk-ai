"""Validador do texto livre do LLM (SPEC, seção 7, item 3)."""

import pytest

from app.domain.validator import Rejection, validate_phrase

CODES = frozenset({"CASA12", "CASA07", "SOBRADO-1"})


@pytest.mark.parametrize(
    "text",
    [
        "Que bom falar com você! Separei algumas opções que combinam com o que você pediu.",
        "Encontrei uma casa que pode te interessar.",
        "Qualquer dúvida, é só me chamar por aqui.",
        "Fico à disposição para ajudar no que precisar.",
        "Dezembro costuma ser bem movimentado por aqui.",  # mês sem dia é permitido
    ],
)
def test_accepts_plain_phrases(text: str) -> None:
    assert validate_phrase(text, property_codes=CODES).ok


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("A casa sai por 1500 a diária.", Rejection.DIGITS),
        ("São 3 opções.", Rejection.DIGITS),
        ("O valor é de dois mil reais.", Rejection.NUMBER_WORDS),
        ("Tem três quartos.", Rejection.NUMBER_WORDS),
        ("Para doze pessoas temos opções.", Rejection.NUMBER_WORDS),
        ("Fica livre de vinte e oito de dezembro em diante.", Rejection.NUMBER_WORDS),
        ("Chegando no primeiro de janeiro.", Rejection.DATE),
        ("A CASA12 é ótima.", Rejection.PROPERTY_CODE),
        ("Veja o sobrado-1.", Rejection.PROPERTY_CODE),
        ("Sua reserva está confirmada!", Rejection.FORBIDDEN_WORD),
        ("Garantido para você.", Rejection.FORBIDDEN_WORD),
        ("Já está reservado.", Rejection.FORBIDDEN_WORD),
        ("Consigo um desconto especial.", Rejection.FORBIDDEN_WORD),
        ("Eu prometo que vai dar certo.", Rejection.FORBIDDEN_WORD),
        ("Confirmado, pode vir.", Rejection.FORBIDDEN_WORD),
        ("Pague em R$ cinquenta.", Rejection.MONEY),
        ("Veja em https://fotos.exemplo.com", Rejection.LINK),
        ("", Rejection.EMPTY),
        ("   ", Rejection.EMPTY),
    ],
)
def test_rejects(text: str, reason: Rejection) -> None:
    result = validate_phrase(text, property_codes=CODES)
    assert not result.ok
    assert reason in result.reasons


def test_rejects_too_long() -> None:
    result = validate_phrase("Olá! " * 80, property_codes=CODES)
    assert Rejection.TOO_LONG in result.reasons


def test_forbidden_words_ignore_case_and_accents() -> None:
    assert not validate_phrase("CONFIRMADA a casa", property_codes=CODES).ok
    assert not validate_phrase("Tudo garantído", property_codes=CODES).ok


def test_article_um_uma_is_allowed() -> None:
    assert validate_phrase("Encontrei uma opção e um ótimo lugar.", property_codes=CODES).ok


def test_property_code_inside_word_is_not_a_false_positive() -> None:
    assert validate_phrase(
        "Casacos são bem-vindos no inverno.", property_codes=frozenset({"CASA"})
    ).ok


def test_result_lists_all_reasons() -> None:
    result = validate_phrase("Confirmado: CASA12 por 1500", property_codes=CODES)
    assert set(result.reasons) >= {
        Rejection.FORBIDDEN_WORD,
        Rejection.PROPERTY_CODE,
        Rejection.DIGITS,
    }
