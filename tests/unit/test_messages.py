import pytest

from app.domain.messages import is_resume, is_stop


@pytest.mark.parametrize("text", ["PARAR", "parar", " Pare! ", "stop", "SAIR."])
def test_stop_words(text: str) -> None:
    assert is_stop(text)


@pytest.mark.parametrize("text", ["parar na praia", "quero parar de procurar", "", "1"])
def test_not_stop(text: str) -> None:
    assert not is_stop(text)


def test_resume() -> None:
    assert is_resume("Voltar") and not is_resume("voltar amanhã")
