"""Logs em JSON, um evento por linha, sem dados pessoais (SPEC, seção 5).

Todo evento carrega `tenant_id`, `conversation_id` e `trace_id` (vazios quando
não se aplicam). Valores de chaves sensíveis são descartados e todo texto passa
pelo mascaramento de dados pessoais.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any, Final

import structlog
from structlog.typing import EventDict, WrappedLogger

from app.domain.masking import mask_pii

CONTEXT_KEYS: Final = ("tenant_id", "conversation_id", "trace_id")

REDACTED: Final = "[REMOVIDO]"
SENSITIVE_KEYS: Final = frozenset(
    {
        "authorization",
        "body",
        "cookie",
        "email",
        "name",
        "password",
        "phone",
        "profile_name",
        "secret",
        "text",
        "token",
        "totp",
        "transcript",
        "wa_id",
    }
)


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, Mapping):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value("", item) for item in value]
    return value


def redact_pii(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Processador structlog: remove chaves sensíveis e mascara texto livre."""
    return {key: _redact_value(key, value) for key, value in event_dict.items()}


def ensure_context_keys(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for key in CONTEXT_KEYS:
        event_dict.setdefault(key, None)
    return event_dict


def configure_logging(level: str = "INFO", *, stream: Any = None) -> None:
    """Configura structlog e o logging padrão (uvicorn, sqlalchemy) para JSON."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        ensure_context_keys,
        structlog.processors.format_exc_info,
        redact_pii,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn configura handlers próprios; redireciona tudo para o formato JSON.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # O acesso já é registrado pelo TraceMiddleware (com trace_id e duração).
    logging.getLogger("uvicorn.access").disabled = True


def bind_context(**values: str | None) -> None:
    """Associa `tenant_id`, `conversation_id` ou `trace_id` aos próximos eventos."""
    unknown = set(values) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"chaves de contexto desconhecidas: {sorted(unknown)}")
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


__all__ = [
    "CONTEXT_KEYS",
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
]
