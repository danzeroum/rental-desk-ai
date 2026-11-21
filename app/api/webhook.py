"""Webhook oficial do WhatsApp (RF01).

GET: desafio de verificação. POST: confere a assinatura do corpo bruto, grava as
mensagens (idempotente) e responde 200 rápido; o processamento fica com o worker.
Erro interno devolve 500 para a Meta reenviar (a idempotência evita duplicar).
"""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from typing import Final

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.adapters.whatsapp_webhook import parse_webhook, signature_is_valid
from app.core.config import Settings
from app.core.logging import get_logger
from app.workers.ingest import ingest_batch

MAX_BODY_BYTES: Final = 1_000_000

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhook"])
log = get_logger(__name__)


def _secret(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter()) if callable(getter) else ""


@router.get("", response_class=PlainTextResponse)
async def verify(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge", max_length=200),
) -> str:
    settings: Settings = request.app.state.settings
    expected = _secret(settings.whatsapp_verify_token)
    if mode != "subscribe" or not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "verificação recusada")
    return challenge


@router.post("")
async def receive(request: Request) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "corpo grande demais")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "corpo grande demais")
    secret = _secret(settings.whatsapp_app_secret)
    if not signature_is_valid(secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("webhook_assinatura_invalida", bytes=len(body))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "assinatura inválida")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON inválido") from None
    batch = parse_webhook(payload)
    await ingest_batch(
        batch,
        sessions=request.app.state.sessions,
        queue=request.app.state.queue,
        now=datetime.now(UTC),
    )
    return {"status": "ok"}
