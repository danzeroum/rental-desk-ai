from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.health import HealthInput, Level, PlatformInput, assess, assess_platform

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
        "sem sincronizar": {"failing_syncs": 1},
    }
    for word, change in cases.items():
        health = assess(replace(HEALTHY, **change), NOW)  # type: ignore[arg-type]
        assert health.level is Level.ALERT and word in health.reasons[0], word


def test_demo_tenant_is_not_blamed_for_silence() -> None:
    quiet = replace(HEALTHY, last_inbound_at=None, demo=True)
    assert assess(quiet, NOW).level is Level.OK


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


def test_platform_alerts() -> None:

    fine = PlatformInput(
        queue_backlog=99,
        disk_used_ratio=0.79,
        backup_at=NOW - timedelta(hours=25),
        backup_remote=True,
    )
    assert assess_platform(fine, NOW) == []
    bad = PlatformInput(
        queue_backlog=500, disk_used_ratio=0.91, backup_at=None, backup_remote=False
    )
    alerts = {a.key: a for a in assess_platform(bad, NOW)}
    assert set(alerts) == {"fila", "disco", "backup"}
    assert all(a.level is Level.ERROR for a in alerts.values())
    local_only = PlatformInput(
        queue_backlog=150, disk_used_ratio=0.85, backup_at=NOW, backup_remote=False
    )
    levels = {a.key: a.level for a in assess_platform(local_only, NOW)}
    assert levels == {"fila": Level.ALERT, "disco": Level.ALERT, "backup_externo": Level.ALERT}


def test_llm_failures_alert() -> None:
    health = assess(replace(HEALTHY, llm_failures_1h=3), NOW)
    assert health.level is Level.ALERT and "contingência" in health.reasons[0]
