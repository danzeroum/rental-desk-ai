"""Monta os adaptadores a partir da configuração (provedor por variável de ambiente)."""

from __future__ import annotations

from app.adapters.fake_llm import FakeLLM
from app.adapters.llm import AnthropicLLMClient, LLMClient
from app.adapters.transcriber import FakeTranscriber, OpenAICompatibleTranscriber, Transcriber
from app.adapters.whatsapp import CloudApiGateway, FakeWhatsAppGateway, WhatsAppGateway
from app.core.config import LLMProvider, Settings, TranscriberProvider, WhatsAppProvider
from app.core.crypto import Keyring


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


def build_keyring(settings: Settings) -> Keyring:
    return Keyring.parse(settings.data_encryption_keys.get_secret_value())
