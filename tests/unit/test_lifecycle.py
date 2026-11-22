from datetime import UTC, datetime, timedelta

import pytest

from app.domain.lifecycle import Readiness, TransitionError, check_transition

NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)
READY = Readiness(
    has_approved_data=True,
    has_number=True,
    shadow_since=NOW - timedelta(days=2),
    shadow_responses=5,
)


def test_normal_path() -> None:
    check_transition("onboarding", "shadow", READY, NOW, early=False)
    check_transition("shadow", "active", READY, NOW, early=False)
    check_transition("active", "shadow", READY, NOW, early=False)


@pytest.mark.parametrize(
    ("current", "target", "ready", "early", "message"),
    [
        ("onboarding", "active", READY, True, "não é permitida"),
        ("suspended", "active", READY, True, "não é permitida"),
        ("onboarding", "shadow", Readiness(False, True, None, 0), False, "planilha"),
        ("onboarding", "shadow", Readiness(True, False, None, 0), False, "número"),
        ("shadow", "active", Readiness(True, True, NOW, 0), True, "Ainda não há respostas"),
        (
            "shadow",
            "active",
            Readiness(True, True, NOW - timedelta(days=2) + timedelta(minutes=1), 3),
            False,
            "2 dias",
        ),
    ],
)
def test_refusals(current: str, target: str, ready: Readiness, early: bool, message: str) -> None:
    with pytest.raises(TransitionError, match=message):
        check_transition(current, target, ready, NOW, early=early)


def test_early_activation_with_confirmation() -> None:
    check_transition(
        "shadow", "active", Readiness(True, True, NOW - timedelta(hours=3), 2), NOW, early=True
    )
