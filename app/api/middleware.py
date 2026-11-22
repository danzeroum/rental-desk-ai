"""Middleware de rastreio: um `trace_id` por requisição, propagado nos logs."""

from __future__ import annotations

import re
import time
import uuid
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_context, clear_context, get_logger

TRACE_HEADER: Final = "X-Request-ID"
QUIET_PATHS: Final = frozenset({"/health", "/health/live"})
"""Chamados a cada poucos segundos por Docker, Caddy e monitor: registrados em DEBUG."""
_VALID_TRACE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")

log = get_logger(__name__)


def _trace_id_from(request: Request) -> str:
    incoming = request.headers.get(TRACE_HEADER, "")
    return incoming if _VALID_TRACE.match(incoming) else uuid.uuid4().hex


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        clear_context()
        trace_id = _trace_id_from(request)
        bind_context(trace_id=trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("requisicao_falhou", method=request.method, path=request.url.path)
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers[TRACE_HEADER] = trace_id
        emit = log.debug if request.url.path in QUIET_PATHS else log.info
        emit(
            "requisicao",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=elapsed_ms,
        )
        return response
