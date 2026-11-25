"""Regras do rastreio sem banco (app/domain/trace.py)."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.access import ACCESS_EVENTS
from app.domain.trace import (
    CSV_HEADER,
    EVENT_LABELS,
    GROUP_LABELS,
    GROUPS,
    TrailEvent,
    csv_line,
    export_cells,
    mask_phone,
    parse_filters,
    phone_variants,
    row_for,
    summarize,
)

AT = datetime(2026, 11, 24, 19, 28, tzinfo=UTC)


def _event(event: str, payload: dict[str, object]) -> TrailEvent:
    return TrailEvent(32, AT, event, uuid.uuid4(), payload, b"\x01" * 32, b"\x00" * 32)


def test_csv_neutralizes_formulas_and_quotes() -> None:
    line = csv_line(['=HYPERLINK("x")', "+1", "normal", 7, None])
    assert line == '"\'=HYPERLINK(""x"")","\'+1","normal","7",""\r\n'
    assert next(csv.reader(io.StringIO(line)))[0] == '\'=HYPERLINK("x")'


def test_mask_phone() -> None:
    assert mask_phone("+5511996685998") == "+55 11 9****-5998"
    assert mask_phone("123") == "****"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        # com o 9: procura sem também (e como foi digitado)
        ("11996685998", ["+11996685998", "+551196685998", "+5511996685998"]),
        ("5511 9668-5998", ["+551196685998", "+5511996685998"]),  # sem o 9: procura com
        ("+1 555 146 5568", ["+15551465568", "+5515551465568"]),  # número de teste da Meta
    ],
)
def test_phone_variants_cover_the_ninth_digit(typed: str, expected: list[str]) -> None:
    assert phone_variants(typed) == expected


def test_every_group_event_has_a_label_and_groups_have_names() -> None:
    assert set(GROUP_LABELS) == set(GROUPS)
    assert GROUPS["acessos"] == ACCESS_EVENTS
    for events in GROUPS.values():
        assert all(event in EVENT_LABELS for event in events)


def test_parse_filters_defaults_and_invalid_ids() -> None:
    tenant = uuid.uuid4()
    filters = parse_filters(str(tenant), "1 ano", "inventado", "(11) 99668-5998", None)
    assert filters.tenant_id == tenant and filters.period == "24h" and filters.group == ""
    assert filters.phone == "11996685998" and filters.reason is None
    with_reason = parse_filters(str(tenant), "7d", "acessos", "", None, motivo="chamado")
    assert with_reason.period == "7d" and with_reason.group == "acessos"
    assert with_reason.as_audit()["reason"] == "chamado"
    with pytest.raises(ValueError, match="badly formed"):
        parse_filters("não-é-uuid", "24h", "", "", None)


def test_summaries_are_readable_and_masked() -> None:
    failed = {"status": "failed", "error_code": 130497, "error": "Business account is restricted"}
    assert summarize("delivery_status", failed).startswith("NÃO ENTREGUE: código 130497: a Meta")
    assert summarize("delivery_status", {"status": "read"}) == "lida"
    reply = summarize("response_sent", {"text": "Ligue  para\n11 99668-5998"})
    assert reply == "Ligue para [TELEFONE]"
    access = {"reason": "chamado", "reason_detail": "nº 7", "phone": "+55 11 9****-5998"}
    assert (
        summarize("trace_viewed", access) == "Chamado do cliente: nº 7 · hóspede +55 11 9****-5998"
    )
    assert summarize("support_access", {}) == "sem motivo registrado"
    other = summarize("block_added", {"user_id": "x", "code": "CASA01", "nota": "tel 11996685998"})
    assert "user_id" not in other and "CASA01" in other and "996685998" not in other


def test_row_and_csv_cells() -> None:
    event = _event("delivery_status", {"status": "failed", "error_code": 131047})
    row = row_for(event)
    assert row.failed and row.label == EVENT_LABELS["delivery_status"] and row.seq == 32
    cells = export_cells(event)
    assert len(cells) == len(CSV_HEADER)
    assert cells[1] == AT.isoformat() and cells[6] == "01" * 32 and cells[7] == "00" * 32
    assert json.loads(str(cells[5])) == {"status": "failed", "error_code": 131047}
