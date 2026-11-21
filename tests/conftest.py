"""Fixtures compartilhadas.

Testes de integração usam Postgres e Redis reais, apontados por
TEST_DATABASE_URL / TEST_DATABASE_ADMIN_URL / TEST_REDIS_URL
(padrão: `make test-deps-up`).
"""

from __future__ import annotations

import os

import pytest

from app.core.config import Environment, Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://rentaldesk_app:app_pw@localhost:55432/rentaldesk_test"
)
TEST_DATABASE_MIGRATION_URL = os.environ.get(
    "TEST_DATABASE_MIGRATION_URL",
    "postgresql://rentaldesk_owner:owner_pw@localhost:55432/rentaldesk_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")
TEST_SECRET_KEY = "x" * 48


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": Environment.TEST,
        "secret_key": TEST_SECRET_KEY,
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        "health_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()
