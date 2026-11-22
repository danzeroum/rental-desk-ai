"""Monta os adaptadores a partir da configuração (provedor por variável de ambiente)."""

from __future__ import annotations

from app.adapters.fake_llm import FakeLLM
from app.adapters.google_sheet import (
    DisabledSheetSource,
    GoogleSheetSource,
    SheetError,
    SheetSource,
)
from app.adapters.http_fetch import FakeFetcher, Fetcher, HttpFetcher
from app.adapters.link_checker import FakeLinkChecker, HttpLinkChecker, LinkChecker
from app.adapters.llm import AnthropicLLMClient, LLMClient
from app.adapters.mailer import FakeMailer, Mailer, SmtpMailer
from app.adapters.transcriber import FakeTranscriber, OpenAICompatibleTranscriber, Transcriber
from app.adapters.whatsapp import CloudApiGateway, FakeWhatsAppGateway, WhatsAppGateway
from app.core.config import (
    Environment,
    LLMProvider,
    Settings,
    TranscriberProvider,
    WhatsAppProvider,
)
from app.core.crypto import Keyring
from app.core.logging import get_logger

log = get_logger(__name__)


def build_gateway(settings: Settings) -> WhatsAppGateway:
    if settings.whatsapp_provider is WhatsAppProvider.CLOUD_API:
        return CloudApiGateway(
            base_url=settings.whatsapp_api_base_url,
            auth_header=settings.whatsapp_auth_header,
            auth_scheme=settings.whatsapp_auth_scheme,
        )
    return FakeWhatsAppGateway()


def build_transcriber(settings: Settings) -> Transcriber:
    if settings.transcriber_provider is TranscriberProvider.OPENAI_COMPATIBLE:
        key = (
            settings.transcriber_api_key.get_secret_value() if settings.transcriber_api_key else ""
        )
        return OpenAICompatibleTranscriber(
            url=settings.transcriber_api_url, api_key=key, model=settings.transcriber_model
        )
    return FakeTranscriber()


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider is LLMProvider.ANTHROPIC:
        if not settings.llm_model:
            raise ValueError("LLM_MODEL é obrigatório com LLM_PROVIDER=anthropic")
        return AnthropicLLMClient(
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
            effort=settings.llm_effort,
            timeout_seconds=settings.llm_timeout_seconds,
            refusal_fallback=settings.llm_refusal_fallback,
        )
    return FakeLLM()


def build_mailer(settings: Settings) -> Mailer:
    if not settings.smtp_host:
        return FakeMailer()
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else "",
        sender=settings.smtp_sender,
    )


def build_keyring(settings: Settings) -> Keyring:
    return Keyring.parse(settings.data_encryption_keys.get_secret_value())


def build_link_checker(settings: Settings) -> LinkChecker:
    """Testes não saem para a internet; os demais ambientes conferem os links de fotos."""
    return FakeLinkChecker() if settings.env == Environment.TEST else HttpLinkChecker()


def build_fetcher(settings: Settings) -> Fetcher:
    """Calendários iCal: testes não saem para a internet."""
    return FakeFetcher() if settings.env == Environment.TEST else HttpFetcher()


def build_sheet_source(settings: Settings) -> SheetSource:
    """Sem conta de serviço (ou com JSON inválido) a ligação com o Google fica desligada: o
    resto do sistema sobe normalmente e o erro aparece no log (RF27 é opcional)."""
    raw = settings.google_service_account_json
    if raw is None or not raw.get_secret_value().strip():
        return DisabledSheetSource()
    try:
        return GoogleSheetSource(raw.get_secret_value())
    except SheetError:
        log.error("conta_de_servico_google_invalida")
        return DisabledSheetSource()
