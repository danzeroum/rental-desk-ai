"""Configuração da aplicação, lida só do ambiente (12-Factor)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TIMEZONE = ZoneInfo("America/Sao_Paulo")
"""Fuso de tudo que é "hoje", "9h" e "24 h" (SPEC, seção 6)."""

MIN_SECRET_KEY_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Todas as variáveis aceitas estão documentadas em `.env.example`."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    env: Environment = Environment.DEVELOPMENT
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    secret_key: SecretStr
    public_base_url: str = "https://localhost"

    database_url: PostgresDsn
    """Papel da aplicação: sem BYPASSRLS. Usado no caminho de requisição."""
    redis_url: RedisDsn

    db_pool_size: int = Field(default=10, ge=1, le=100)
    health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @property
    def is_production_like(self) -> bool:
        return self.env in {Environment.STAGING, Environment.PRODUCTION}

    @model_validator(mode="after")
    def _check_secrets(self) -> Self:
        if len(self.secret_key.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            msg = f"SECRET_KEY precisa ter pelo menos {MIN_SECRET_KEY_LENGTH} caracteres"
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # valores vêm do ambiente
