import pytest
from pydantic import ValidationError

from app.core.config import TIMEZONE, Environment, Settings, get_settings
from tests.conftest import make_settings


def test_timezone_is_sao_paulo() -> None:
    assert TIMEZONE.key == "America/Sao_Paulo"


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(secret_key="curta")


def test_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/x")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("ENV", "staging")
    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()
    assert settings.env is Environment.STAGING
    assert settings.is_production_like
    assert "s" * 40 not in repr(settings)


def test_settings_are_immutable(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]
