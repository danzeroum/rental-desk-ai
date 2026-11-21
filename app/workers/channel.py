"""Credencial do número de WhatsApp de uma imobiliária (token cifrado no banco)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.whatsapp import Channel
from app.core.crypto import Keyring
from app.models import WhatsAppAccount


class ChannelMissingError(Exception):
    """Imobiliária sem número de WhatsApp conectado."""


def token_context(tenant_id: uuid.UUID) -> str:
    return f"whatsapp_token:{tenant_id}"


def seal_token(keyring: Keyring, tenant_id: uuid.UUID, token: str) -> bytes:
    return keyring.encrypt(token, context=token_context(tenant_id))


async def channel_for(session: AsyncSession, tenant_id: uuid.UUID, keyring: Keyring) -> Channel:
    account = await session.scalar(
        select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant_id).limit(1)
    )
    if account is None:
        raise ChannelMissingError("imobiliária sem número de WhatsApp conectado")
    token = (
        keyring.decrypt(account.access_token_enc, context=token_context(tenant_id))
        if account.access_token_enc
        else ""
    )
    return Channel(phone_number_id=account.phone_number_id, access_token=token)
