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
TEST_DATABASE_OPS_URL = os.environ.get(
    "TEST_DATABASE_OPS_URL", "postgresql://rentaldesk_ops:ops_pw@localhost:55432/rentaldesk_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")
TEST_SECRET_KEY = "x" * 48
TEST_ENCRYPTION_KEYS = "k1:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="  # 32 bytes "A", só testes


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": Environment.TEST,
        "secret_key": TEST_SECRET_KEY,
        "data_encryption_keys": TEST_ENCRYPTION_KEYS,
        "whatsapp_app_secret": "segredo-do-app-de-teste",
        "whatsapp_verify_token": "token-de-verificacao",
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        "health_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def production_settings(**overrides: object) -> Settings:
    """Produção com todos os obrigatórios preenchidos (provedores reais, segredos)."""
    values: dict[str, object] = {
        "env": Environment.PRODUCTION,
        "whatsapp_provider": "cloud_api",
        "transcriber_provider": "openai_compatible",
        "transcriber_api_key": "chave-de-teste",
    }
    values.update(overrides)
    return make_settings(**values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()
