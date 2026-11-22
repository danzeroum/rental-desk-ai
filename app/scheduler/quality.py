"""Qualidade do número na Meta (RF23), consultada a cada hora para o painel do operador."""

from __future__ import annotations

import uuid

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.whatsapp import GatewayError, WhatsAppGateway
from app.core.crypto import Keyring
from app.core.logging import bind_context, get_logger
from app.core.rls import tenant_session
from app.models import WhatsAppAccount
from app.workers.channel import ChannelMissingError, channel_for

QUALITY_EVERY_SECONDS = 3600
log = get_logger(__name__)


async def tenants_with_number(ops_engine: AsyncEngine) -> list[uuid.UUID]:
    async with ops_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT DISTINCT tenant_id FROM whatsapp_account WHERE access_token_enc IS NOT NULL"
            )
        )
        return [row[0] for row in rows]


async def refresh_quality(
    sessions: async_sessionmaker[AsyncSession],
    gateway: WhatsAppGateway,
    keyring: Keyring,
    tenant_id: uuid.UUID,
) -> bool:
    bind_context(tenant_id=str(tenant_id))
    try:
        async with tenant_session(sessions, tenant_id) as session:
            channel = await channel_for(session, tenant_id, keyring)
        rating = await gateway.phone_quality(channel)
    except (GatewayError, ChannelMissingError) as exc:
        log.warning("qualidade_numero_falhou", error_type=type(exc).__name__)
        return False
    async with tenant_session(sessions, tenant_id) as session:
        await session.execute(
            update(WhatsAppAccount)
            .where(WhatsAppAccount.phone_number_id == channel.phone_number_id)
            .values(quality_rating=rating)
        )
    if rating != "GREEN":
        log.warning("qualidade_numero_baixa", rating=rating)
    return True
