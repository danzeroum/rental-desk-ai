from datetime import UTC, datetime, timedelta

from app.domain.retention import ANONYMIZED_MARK, cutoff, expired, scrub

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)


def test_expires_only_after_the_full_period() -> None:
    assert cutoff(NOW, 180) == NOW - timedelta(days=180)
    assert not expired(NOW - timedelta(days=180), NOW, 180)  # no dia exato, ainda não
    assert not expired(NOW - timedelta(days=179, hours=23, minutes=59), NOW, 180)
    assert expired(NOW - timedelta(days=180, minutes=1), NOW, 180)


def test_scrub_removes_phone_with_and_without_plus_and_profile_name() -> None:
    text = "Sou Maria Souza, meu número é +5511996685998 ou 5511996685998"
    cleaned = scrub(text, "+5511996685998", "Maria Souza")
    assert cleaned is not None
    assert "5511996685998" not in cleaned and "Maria Souza" not in cleaned
    assert cleaned.count(ANONYMIZED_MARK) == 3


def test_scrub_ignores_missing_or_tiny_values() -> None:
    assert scrub(None, "+5511996685998", "Ana") is None
    assert scrub("casa para Jo e eu", None, "Jo") == "casa para Jo e eu"
