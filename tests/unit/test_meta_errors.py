from app.domain.meta_errors import META_ERRORS, explain, short


def test_known_code_says_what_happened_and_what_to_do() -> None:
    text = explain(130497, "Business account is restricted from messaging users in this country.")
    assert text.startswith("código 130497: a Meta bloqueou envios")
    assert "O que fazer: conclua a verificação da empresa" in text
    assert "Business account" not in text  # o original fica na trilha e no CSV


def test_unknown_code_keeps_the_meta_text() -> None:
    assert explain(999999, "Something new") == "código 999999 da Meta (Something new)"
    assert explain(999999) == "código 999999 da Meta (sem motivo informado)"
    assert explain(None, None) == "sem motivo informado"


def test_short_version_for_health() -> None:
    assert short(None) == ""
    assert short(190) == "código 190 da Meta: o token de acesso da Meta é inválido ou venceu"
    assert short(42) == "código 42 da Meta"


def test_every_entry_is_complete() -> None:
    assert all(e.meaning and e.action for e in META_ERRORS.values())
