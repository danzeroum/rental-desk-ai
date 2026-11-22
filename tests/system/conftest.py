"""Testes de sistema usam o mesmo banco migrado e os mesmos papéis da integração."""

from tests.integration.conftest import (  # noqa: F401 - fixtures
    app_engine,
    app_sessions,
    migrated_database,
    ops_engine,
    ops_sessions,
    owner_engine,
)
