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


class WhatsAppProvider(StrEnum):
    CLOUD_API = "cloud_api"  # Cloud API da Meta, direta ou via parceiro oficial (BSP)
    FAKE = "fake"


class TranscriberProvider(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"  # POST multipart em /audio/transcriptions
    FAKE = "fake"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    FAKE = "fake"


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

    data_encryption_keys: SecretStr
    """Chaves AES-256 dos segredos no banco: `k1:<base64>[,k0:<base64>]` (D022)."""

    # WhatsApp (formato da Cloud API; URL e cabeçalho configuráveis para BSP ou Meta direta)
    whatsapp_provider: WhatsAppProvider = WhatsAppProvider.FAKE
    whatsapp_api_base_url: str = "https://graph.facebook.com/v21.0"
    whatsapp_auth_header: str = Field(default="Authorization", pattern=r"^[A-Za-z0-9-]{1,40}$")
    whatsapp_auth_scheme: str = Field(default="Bearer", max_length=20)
    """Prefixo antes do token ("Bearer"); vazio para parceiros que usam só a chave."""
    whatsapp_app_secret: SecretStr | None = None
    """Segredo do app para conferir `X-Hub-Signature-256` do webhook (RF01)."""
    whatsapp_verify_token: SecretStr | None = None
    """Token do desafio de verificação (GET) do webhook."""

    # Transcrição de áudio (API sem uso para treino; SPEC, seção 0b)
    transcriber_provider: TranscriberProvider = TranscriberProvider.FAKE
    transcriber_api_url: str = "https://api.openai.com/v1/audio/transcriptions"
    transcriber_api_key: SecretStr | None = None
    transcriber_model: str = "whisper-1"

    # LLM (extração e frases curtas); o modelo é escolhido pelo conjunto de ouro (SPEC, 0b)
    llm_provider: LLMProvider = LLMProvider.FAKE
    llm_model: str = Field(default="", max_length=80)
    llm_api_key: SecretStr | None = None
    llm_effort: str = Field(default="low", pattern=r"^(low|medium|high|xhigh|max)$")
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    llm_refusal_fallback: bool = True

    # E-mail (convites, recuperação de senha, relatório, alertas ao operador)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_sender: str = "rental-desk-ai <nao-responda@localhost>"

    # Fila e atendimento (RF05, RF20)
    debounce_quiet_seconds: float = Field(default=6.0, gt=0, le=60)
    debounce_max_seconds: float = Field(default=20.0, gt=0, le=120)
    rate_limit_per_minute: int = Field(default=15, ge=1, le=1000)
    rate_limit_silence_seconds: int = Field(default=600, ge=1, le=86400)

    @property
    def is_production_like(self) -> bool:
        return self.env in {Environment.STAGING, Environment.PRODUCTION}

    @model_validator(mode="after")
    def _check_secrets(self) -> Self:
        if len(self.secret_key.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            msg = f"SECRET_KEY precisa ter pelo menos {MIN_SECRET_KEY_LENGTH} caracteres"
            raise ValueError(msg)
        if self.debounce_max_seconds < self.debounce_quiet_seconds:
            raise ValueError("DEBOUNCE_MAX_SECONDS não pode ser menor que DEBOUNCE_QUIET_SECONDS")
        if self.env is Environment.PRODUCTION:
            self._check_production()
        return self

    def _check_production(self) -> None:
        if self.whatsapp_provider is WhatsAppProvider.FAKE:
            raise ValueError("WHATSAPP_PROVIDER=fake não é permitido em produção")
        if self.transcriber_provider is TranscriberProvider.FAKE:
            raise ValueError("TRANSCRIBER_PROVIDER=fake não é permitido em produção")
        if self.llm_provider is LLMProvider.FAKE:
            raise ValueError("LLM_PROVIDER=fake não é permitido em produção")
        if not self.llm_model:
            raise ValueError("obrigatório em produção: LLM_MODEL")
        missing = [
            name
            for name, value in (
                ("WHATSAPP_APP_SECRET", self.whatsapp_app_secret),
                ("WHATSAPP_VERIFY_TOKEN", self.whatsapp_verify_token),
                ("TRANSCRIBER_API_KEY", self.transcriber_api_key),
                ("LLM_API_KEY", self.llm_api_key),
            )
            if value is None or not value.get_secret_value()
        ]
        if missing:
            raise ValueError(f"obrigatório em produção: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # valores vêm do ambiente
