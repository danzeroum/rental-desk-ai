"""Migrações: modelos e banco sem divergência; ida e volta limpas."""

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import TEST_DATABASE_MIGRATION_URL

pytestmark = pytest.mark.integration


@pytest.fixture
def alembic_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_MIGRATION_URL", TEST_DATABASE_MIGRATION_URL)
    return Config("alembic.ini")


def test_models_match_migrations(alembic_config: Config) -> None:
    command.check(alembic_config)  # levanta erro se um modelo mudou sem migração
