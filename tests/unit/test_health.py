from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.health import HealthInput, Level, assess

NOW = datetime(2026, 12, 20, 15, 0, tzinfo=UTC)
HEALTHY = HealthInput(
    active=True, paused=False, last_inbound_at=NOW - timedelta(hours=1),
    availability_updated_at=NOW - timedelta(hours=23, minutes=59), open_handoffs=1,
    oldest_open_handoff_at=NOW - timedelta(minutes=15), validator_rejections_24h=0,
    stuck_messages=0, dead_jobs=0, quality="GREEN", has_approved_data=True,
)  # fmt: skip


def test_healthy_at_the_limits() -> None:
    assert assess(HEALTHY, NOW).level is Level.OK


def test_alerts() -> None:
    cases = {
        "pausado": {"paused": True},
        "amarela": {"quality": "yellow"},
        "vencidas": {"availability_updated_at": NOW - timedelta(hours=24, minutes=1)},
        "15 min": {"oldest_open_handoff_at": NOW - timedelta(minutes=16)},
        "validador": {"validator_rejections_24h": 2},
        "48 h": {"last_inbound_at": None},
    }
    for word, change in cases.items():
        health = assess(replace(HEALTHY, **change), NOW)  # type: ignore[arg-type]
        assert health.level is Level.ALERT and word in health.reasons[0], word


def test_errors_come_first() -> None:
    health = assess(replace(HEALTHY, dead_jobs=1, paused=True, quality="RED"), NOW)
    assert health.level is Level.ERROR
    assert health.reasons[0].startswith("1 tarefa") and "vermelha" in health.reasons[1]
    assert "pausado" in health.reasons[-1]


def test_onboarding_tenant_is_not_blamed_for_silence() -> None:
    quiet = replace(
        HEALTHY,
        active=False,
        last_inbound_at=None,
        availability_updated_at=None,
        has_approved_data=False,
    )
    assert assess(quiet, NOW).level is Level.OK
    assert "sem planilha" in assess(replace(quiet, active=True), NOW).reasons[0]
