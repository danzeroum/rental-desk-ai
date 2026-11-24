import pytest

from app.core.masking import mask_pii, mask_structured


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("meu cpf é 123.456.789-09", "meu cpf é [CPF]"),
        ("cpf 12345678909 ok", "cpf [CPF] ok"),
        ("cnpj 12.345.678/0001-95", "cnpj [CNPJ]"),
        ("cartão 4111 1111 1111 1111 validade", "cartão [CARTAO] validade"),
        ("cartão 4111-1111-1111-1111", "cartão [CARTAO]"),
        ("cartão 4111111111111111", "cartão [CARTAO]"),
        ("mande para joao.silva+viagem@exemplo.com.br", "mande para [EMAIL]"),
        ("pix 123e4567-e89b-12d3-a456-426614174000", "pix [CHAVE_PIX]"),
        ("me liga (11) 98765-4321", "me liga [TELEFONE]"),
        ("whats +55 11 98765-4321", "whats [TELEFONE]"),
        ("whats 5511987654321", "whats [TELEFONE]"),
        ("fixo 11 3456-7890", "fixo [TELEFONE]"),
    ],
)
def test_masks_personal_data(raw: str, expected: str) -> None:
    assert mask_pii(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Casa para 12 pessoas de 28/12 a 02/01",
        "CASA12 28/12-02/01",
        "orçamento de R$ 40.000,00",
        "pagamos 2000 reais",
        "check-in às 14h, 3 quartos, 2 suítes",
        "ano 2026",
    ],
)
def test_keeps_business_data(text: str) -> None:
    assert mask_pii(text) == text


def test_empty_string() -> None:
    assert mask_pii("") == ""


def test_multiple_items_in_one_text() -> None:
    text = "cpf 123.456.789-09 e email a@b.co"
    assert mask_pii(text) == "cpf [CPF] e email [EMAIL]"


def test_structured_keeps_ids_and_dates_but_masks_free_text() -> None:
    uid = "0ab2b2f0-c19a-4ea4-990d-cebde3bb8922"
    payload = {
        "user_id": uid, "approved_by": uid, "inbound_message_ids": [uid], "at": "2026-11-24",
        "text": "Meu e-mail é ana@x.com e o CPF 123.456.789-09",
        "nested": {"snapshot_id": uid, "obs": f"chave {uid}"},
    }  # fmt: skip
    masked = mask_structured(payload)
    assert masked["user_id"] == uid and masked["approved_by"] == uid
    assert masked["inbound_message_ids"] == [uid] and masked["nested"]["snapshot_id"] == uid
    assert "ana@x.com" not in masked["text"] and "123.456.789-09" not in masked["text"]
    assert masked["nested"]["obs"] == "chave [CHAVE_PIX]"  # em texto livre, continua mascarado
