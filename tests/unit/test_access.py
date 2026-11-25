from app.domain.access import REASONS, AccessReason, describe_payload, parse_reason


def test_reason_must_be_one_of_the_known_codes() -> None:
    assert parse_reason(None) is None
    assert parse_reason("") is None
    assert parse_reason("curiosidade", "só olhando") is None
    reason = parse_reason("chamado", "  nº 4521   urgente ")
    assert reason == AccessReason("chamado", "nº 4521 urgente")
    assert reason.label == REASONS["chamado"]


def test_detail_is_masked_and_short() -> None:
    reason = parse_reason("comprovacao", "hóspede +5511996685998 diz que não recebeu " + "x" * 200)
    assert reason is not None
    assert "996685998" not in reason.detail and "[TELEFONE]" in reason.detail
    assert len(reason.detail) == 120


def test_describe_for_the_trail() -> None:
    assert AccessReason("incidente").describe() == "Incidente ou falha"
    assert describe_payload({"reason": "chamado", "reason_detail": "nº 7"}) == (
        "Chamado do cliente: nº 7"
    )
    assert describe_payload({"user_id": "x"}) == ""
