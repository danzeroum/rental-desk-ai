"""Webhook da Cloud API: assinatura (RF01) e leitura do payload.

O payload é lido de forma tolerante (campos desconhecidos são ignorados; itens
malformados são descartados um a um) para que um tipo de mensagem novo da Meta
não derrube o lote inteiro.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from app.models.enums import MessageKind

MAX_TEXT_CHARS: Final = 4096
_TEXT_TYPES: Final = {"text", "button", "interactive"}


def signature_is_valid(app_secret: str, body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256: sha256=<hex>` = HMAC-SHA256 do corpo bruto com o app secret."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True)
class InboundMessage:
    """Mensagem do hóspede (campo `messages`)."""

    phone_number_id: str
    external_id: str
    wa_id: str = field(repr=False)
    kind: MessageKind
    timestamp: datetime
    text: str | None = field(default=None, repr=False)
    media_id: str | None = None
    media_mime: str | None = None
    profile_name: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class EchoMessage:
    """Mensagem enviada pelo app WhatsApp Business do corretor (campo `smb_message_echoes`)."""

    phone_number_id: str
    external_id: str
    to_wa_id: str = field(repr=False)
    kind: MessageKind
    timestamp: datetime
    text: str | None = field(default=None, repr=False)


@dataclass
class WebhookBatch:
    messages: list[InboundMessage] = field(default_factory=list)
    echoes: list[EchoMessage] = field(default_factory=list)
    discarded: int = 0


def _timestamp(raw: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(UTC)


def _text_of(item: dict[str, Any]) -> str | None:
    kind = item.get("type")
    text: Any = None
    if kind == "text":
        text = (item.get("text") or {}).get("body")
    elif kind == "button":
        text = (item.get("button") or {}).get("text")
    elif kind == "interactive":
        interactive = item.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = reply.get("title")
    elif kind == "image":
        text = (item.get("image") or {}).get("caption")
    return str(text)[:MAX_TEXT_CHARS] if isinstance(text, str) and text.strip() else None


def _kind_of(item: dict[str, Any]) -> MessageKind:
    kind = item.get("type")
    if kind in _TEXT_TYPES:
        return MessageKind.TEXT
    if kind == "audio":
        return MessageKind.AUDIO
    if kind == "image":
        return MessageKind.IMAGE
    return MessageKind.OTHER


def _media_of(item: dict[str, Any]) -> tuple[str | None, str | None]:
    media = item.get(str(item.get("type"))) if item.get("type") in {"audio", "image"} else None
    if not isinstance(media, dict) or not isinstance(media.get("id"), str):
        return None, None
    mime = media.get("mime_type")
    return media["id"], mime if isinstance(mime, str) else None


def _profiles(value: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in value.get("contacts") or []:
        if isinstance(contact, dict) and isinstance(contact.get("wa_id"), str):
            name = (contact.get("profile") or {}).get("name")
            if isinstance(name, str):
                names[contact["wa_id"]] = name[:120]
    return names


def _inbound(item: Any, phone_number_id: str, profiles: dict[str, str]) -> InboundMessage | None:
    if not isinstance(item, dict):
        return None
    external_id, sender = item.get("id"), item.get("from")
    if not isinstance(external_id, str) or not isinstance(sender, str) or not sender.isdigit():
        return None
    media_id, media_mime = _media_of(item)
    return InboundMessage(
        phone_number_id=phone_number_id,
        external_id=external_id[:128],
        wa_id=sender,
        kind=_kind_of(item),
        timestamp=_timestamp(item.get("timestamp")),
        text=_text_of(item),
        media_id=media_id,
        media_mime=media_mime,
        profile_name=profiles.get(sender),
    )


def _echo(item: Any, phone_number_id: str) -> EchoMessage | None:
    if not isinstance(item, dict):
        return None
    external_id, recipient = item.get("id"), item.get("to")
    if (
        not isinstance(external_id, str)
        or not isinstance(recipient, str)
        or not recipient.isdigit()
    ):
        return None
    return EchoMessage(
        phone_number_id=phone_number_id,
        external_id=external_id[:128],
        to_wa_id=recipient,
        kind=_kind_of(item),
        timestamp=_timestamp(item.get("timestamp")),
        text=_text_of(item),
    )


def parse_webhook(payload: Any) -> WebhookBatch:
    batch = WebhookBatch()
    if not isinstance(payload, dict):
        return batch
    for entry in payload.get("entry") or []:
        for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            if not isinstance(phone_number_id, str):
                batch.discarded += 1
                continue
            if change.get("field") == "messages":
                profiles = _profiles(value)
                for item in value.get("messages") or []:
                    parsed = _inbound(item, phone_number_id, profiles)
                    batch.discarded += parsed is None
                    if parsed is not None:
                        batch.messages.append(parsed)
            elif change.get("field") == "smb_message_echoes":
                for item in value.get("message_echoes") or []:
                    echo = _echo(item, phone_number_id)
                    batch.discarded += echo is None
                    if echo is not None:
                        batch.echoes.append(echo)
    return batch
