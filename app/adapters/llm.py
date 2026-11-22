"""Cliente de LLM (SPEC, seção 3): só "complete em JSON neste esquema".

Usado na extração (intenção + campos) e na redação das duas frases curtas. O
provedor e o modelo vêm do ambiente (`LLM_PROVIDER`, `LLM_MODEL`); o modelo é
escolhido pelo conjunto de ouro, não pelo código.

Qualquer falha (rede, limite, servidor, recusa de segurança, saída cortada) vira
`LLMUnavailableError`; o pipeline responde com a frase de contingência e passa a
conversa ao corretor (seção 7, item 10).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol

import anthropic

from app.core.logging import get_logger

log = get_logger(__name__)

# Modelos que aceitam o fallback de recusa no servidor (`fallbacks: "default"`).
REFUSAL_FALLBACK_MODELS: Final = frozenset(
    {
        "claude-fable-5-2",
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-sonnet-5-5",
    }
)
REFUSAL_FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    """Modelo que de fato respondeu (com o fallback, pode ser outro que o pedido)."""
    fallback_from: str | None = None
    """Modelo que recusou o pedido, quando o fallback de recusa foi acionado."""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0.0

    def audit(self, purpose: str) -> dict[str, Any]:
        return {
            "purpose": purpose,
            "model": self.model,
            "fallback_from": self.fallback_from,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_seconds * 1000),
        }


class LLMUnavailableError(Exception):
    """O provedor não entregou uma resposta utilizável."""


class LLMClient(Protocol):
    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024
    ) -> LLMResponse:
        """Devolve o texto JSON produzido segundo `schema` (validação fica com quem chama)."""
        ...


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        effort: str = "low",
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        refusal_fallback: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model, self.effort = model, effort
        self.refusal_fallback = refusal_fallback and model in REFUSAL_FALLBACK_MODELS
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024
    ) -> LLMResponse:
        started = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        try:
            if self.refusal_fallback:
                response: Any = await self._client.beta.messages.create(
                    **request, betas=[REFUSAL_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self._client.messages.create(**request)
        except anthropic.APIConnectionError as exc:  # inclui timeout
            log.warning("llm_indisponivel", error_type=type(exc).__name__)
            raise LLMUnavailableError("falha de conexão com o provedor de IA") from exc
        except anthropic.APIStatusError as exc:
            log.warning("llm_erro_api", status=exc.status_code, request_id=exc.request_id)
            raise LLMUnavailableError(f"provedor de IA respondeu {exc.status_code}") from exc
        text = self._text_of(response)
        usage = getattr(response, "usage", None)
        fallback_from = next(
            (
                getattr(getattr(block, "from_", None), "model", None)
                for block in response.content
                if getattr(block, "type", "") == "fallback"
            ),
            None,
        )
        if fallback_from is not None:
            log.warning("llm_fallback_de_recusa", recusou=fallback_from, respondeu=response.model)
        return LLMResponse(
            text=text,
            model=str(getattr(response, "model", self.model)),
            fallback_from=fallback_from,
            input_tokens=_tokens(
                usage, "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
            ),
            output_tokens=_tokens(usage, "output_tokens"),
            latency_seconds=time.perf_counter() - started,
        )

    def _text_of(self, response: Any) -> str:
        stop = getattr(response, "stop_reason", None)
        if stop in {"refusal", "max_tokens"}:
            log.warning(
                "llm_resposta_inutil",
                stop_reason=stop,
                request_id=getattr(response, "_request_id", None),
            )
            raise LLMUnavailableError(f"resposta do provedor encerrada por {stop}")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise LLMUnavailableError("resposta do provedor sem texto")
        return str(text)


def _tokens(usage: Any, *fields: str) -> int:
    return sum(int(getattr(usage, f, 0) or 0) for f in fields)


def loads_object(text: str) -> dict[str, Any] | None:
    """JSON de objeto ou `None` (o esquema é conferido depois, pelo domínio)."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
