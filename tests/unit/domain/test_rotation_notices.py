import uuid
from datetime import UTC, datetime

from app.domain.notices import (
    TEMPLATES,
    guest_waiting_text,
    reason_label,
    summary_text,
)
from app.domain.rotation import Broker, next_in_cycle, pick_for_new

A, B, C = (
    Broker(uuid.UUID(int=i), n, f"+55119000000{i}")
    for i, n in ((1, "Ana"), (2, "Bia"), (3, "Caio"))
)
T0 = datetime(2026, 12, 1, 10, tzinfo=UTC)


class TestRotation:
    def test_new_handoff_goes_to_who_never_received(self) -> None:
        assert pick_for_new([A, B, C], {A.user_id: T0, C.user_id: T0}) == B

    def test_then_oldest_assignment(self) -> None:
        later = T0.replace(hour=11)
        assert pick_for_new([A, B, C], {A.user_id: later, B.user_id: T0, C.user_id: later}) == B

    def test_tie_by_registration_order(self) -> None:
        assert pick_for_new([A, B, C], {}) == A

    def test_no_brokers(self) -> None:
        assert pick_for_new([], {}) is None
        assert next_in_cycle([], A.user_id) is None

    def test_cycle(self) -> None:
        assert next_in_cycle([A, B, C], A.user_id) == B
        assert next_in_cycle([A, B, C], C.user_id) == A
        assert next_in_cycle([A], A.user_id) == A  # sozinho: recebe de novo
        assert next_in_cycle([A, B], uuid.uuid4()) == A  # quem estava saiu do plantão


class TestNotices:
    def test_summary_line(self) -> None:
        summary = {
            "pedido": {"guests": 12, "check_in": "2026-12-28", "check_out": "2027-01-02",
                       "required": ["piscina"], "budget_max_cents": 4_000_000},
            "casas_oferecidas": ["CASA06", "CASA01"],
            "casa_escolhida": None,
            "pendencias": ["CASA06: carregador de carro elétrico"],
        }  # fmt: skip
        assert summary_text(summary) == (
            "12 pessoas · 28/12/2026 a 02/01/2027 · piscina · até R$ 40.000,00 · "
            "ofertas: CASA06, CASA01 · a confirmar: CASA06: carregador de carro elétrico"
        )

    def test_summary_with_choice_and_empty(self) -> None:
        assert summary_text({"pedido": {}, "casa_escolhida": "CASA05"}) == "escolheu CASA05"
        assert summary_text({}) == "sem detalhes ainda"

    def test_reason_label(self) -> None:
        assert reason_label("choose") == "escolheu uma casa"
        assert reason_label("desconhecido") == "desconhecido"

    def test_guest_waiting_with_and_without_phone(self) -> None:
        assert guest_waiting_text("+55 13 3333-0000").endswith("ligue para +55 13 3333-0000.")
        assert "ligue" not in guest_waiting_text(None)

    def test_templates_have_one_param_per_placeholder(self) -> None:
        for template in TEMPLATES:
            placeholders = {
                int(p) for p in __import__("re").findall(r"\{\{(\d+)\}\}", template.body)
            }
            assert placeholders == set(range(1, len(template.params) + 1)), template.name
