from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.onboarding import OnboardingFacts, current, steps

NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)
START = OnboardingFacts(False, False, False, False, 0, "onboarding", None)


def test_fresh_tenant_starts_at_step_one() -> None:
    all_steps = steps(START, NOW)
    assert len(all_steps) == 7 and not any(s.done for s in all_steps)
    assert current(all_steps).number == 1  # type: ignore[union-attr]


def test_progress_through_the_steps() -> None:
    f = replace(START, has_number=True, has_saved_version=True, simulator_turns=2)
    assert current(steps(f, NOW)).number == 4  # type: ignore[union-attr]
    f = replace(
        f,
        simulator_turns=3,
        has_approved=True,
        status="shadow",
        shadow_since=NOW - timedelta(days=1),
    )
    assert current(steps(f, NOW)).number == 6  # type: ignore[union-attr]
    f = replace(f, shadow_since=NOW - timedelta(days=2))
    assert current(steps(f, NOW)).number == 7  # type: ignore[union-attr]
    assert current(steps(replace(f, status="active"), NOW)) is None


def test_google_sheet_counts_for_step_two_only() -> None:
    f = replace(START, has_sheet_link=True)
    done = {s.number for s in steps(f, NOW) if s.done}
    assert done == {2}


def test_active_agency_is_complete_even_without_simulator() -> None:
    f = replace(START, has_number=True, has_saved_version=True, has_approved=True, status="active")
    assert current(steps(f, NOW)) is None
