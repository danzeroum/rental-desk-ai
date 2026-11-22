from datetime import UTC, date, datetime, timedelta

from app.domain.hours import BusinessHours
from app.domain.report import (
    ConversationFacts,
    HandoffFacts,
    compute,
    email_text,
    week_before,
    whatsapp_params,
)

HOURS = BusinessHours.from_json({str(d): [["09:00", "18:00"]] for d in range(7)})
MON = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)  # 12h em São Paulo
NIGHT = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)  # 23h em São Paulo
PERIOD = (date(2026, 11, 16), date(2026, 11, 23))


def test_week_before() -> None:
    assert week_before(date(2026, 11, 23)) == PERIOD  # segunda
    assert week_before(date(2026, 11, 29)) == PERIOD  # domingo seguinte


def test_metrics() -> None:
    conversations = [
        ConversationFacts("a", MON, MON + timedelta(seconds=10)),
        ConversationFacts("a", NIGHT, NIGHT + timedelta(seconds=30)),  # mesmo contato
        ConversationFacts("b", NIGHT, None),
    ]
    handoffs = [
        HandoffFacts(MON, MON + timedelta(minutes=4), "won"),
        HandoffFacts(MON, MON + timedelta(minutes=12), "lost"),
        HandoffFacts(MON, None, None),
        HandoffFacts(MON, MON + timedelta(minutes=6), "pending"),
    ]
    m = compute(PERIOD, conversations, handoffs, [150_000, 250_050], HOURS)
    assert (m.contacts, m.out_of_hours_pct) == (2, 67)
    assert (m.first_response_median_s, m.first_response_p95_s) == (10, 30)
    assert (m.handoffs, m.won, m.lost, m.pending, m.without_outcome) == (4, 1, 1, 1, 1)
    assert (m.close_rate_pct, m.assume_median_min, m.offered_value_cents) == (50, 6, 400_050)
    assert m.to_json()["period_start"] == "2026-11-16"


def test_empty_week_has_no_fake_numbers() -> None:
    m = compute(PERIOD, [], [], [], HOURS)
    subject, body = email_text("Imobiliária Sol", m, "https://painel/x")
    assert "16/11/2026 a 22/11/2026" in subject
    assert "Contatos atendidos: 0" in body and "Taxa de fechamento: —" in body
    assert "R$ 0,00" in body and "Painel: https://painel/x" in body


def test_email_asks_for_missing_outcomes() -> None:
    m = compute(PERIOD, [], [HandoffFacts(MON, None, None)], [], HOURS)
    _, body = email_text("Sol", m, "https://painel/p")
    assert "Marque os desfechos que faltam (um clique cada): https://painel/p" in body


def test_whatsapp_params() -> None:
    m = compute(
        PERIOD,
        [ConversationFacts("a", MON, None)],
        [HandoffFacts(MON, None, "won")],
        [100_00],
        HOURS,
    )
    assert whatsapp_params("Sol", m, "https://p") == [
        "Sol",
        "16/11 a 22/11",
        "1",
        "1",
        "1 fecharam, 0 perderam (taxa 100%)",
        "R$ 100,00",
        "https://p",
    ]
