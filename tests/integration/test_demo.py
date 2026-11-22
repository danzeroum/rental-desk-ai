"""Modo demonstração (RF25): criar, renovar e ligar o número de teste."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.demo import DemoError, season_year, seed_demo
from app.models import Property, Tenant, TenantRoute, WhatsAppAccount
from app.workers.channel import token_context

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def test_season_year() -> None:
    assert season_year(date(2026, 11, 22)) == 2026
    assert season_year(date(2027, 2, 10)) == 2026  # Carnaval ainda é a temporada 2026/27
    assert season_year(date(2027, 4, 1)) == 2027


async def test_seed_demo_creates_and_renews(
    app_sessions: Sessions, ops_sessions: Sessions, settings: Settings
) -> None:
    keyring = Keyring.parse(settings.data_encryption_keys.get_secret_value())
    pnid = f"TEST{uuid.uuid4().hex[:8]}"
    with pytest.raises(DemoError):
        await seed_demo(ops_sessions, app_sessions, keyring, now=NOW, phone_number_id=pnid)
    first = await seed_demo(ops_sessions, app_sessions, keyring, now=NOW)
    second = await seed_demo(
        ops_sessions, app_sessions, keyring, now=NOW, phone_number_id=pnid, access_token="tok-teste"
    )
    assert second.tenant_id == first.tenant_id and not second.created
    assert second.snapshot_version == first.snapshot_version + 1 and second.phone_connected
    async with tenant_session(app_sessions, first.tenant_id) as session:
        tenant = await session.get(Tenant, first.tenant_id)
        assert tenant is not None
        houses = await session.scalar(
            select(func.count())
            .select_from(Property)
            .where(Property.snapshot_id == tenant.active_snapshot_id)
        )
        account = await session.scalar(
            select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id == pnid)
        )
        route = await session.scalar(select(TenantRoute).where(TenantRoute.external_id == pnid))
    assert (tenant.name, tenant.is_demo, tenant.status) == (
        "Imobiliária Demo Riviera",
        True,
        "active",
    )
    assert houses == 10 and tenant.availability_updated_at == NOW
    assert account is not None and account.is_test_number and account.access_token_enc
    assert (
        keyring.decrypt(account.access_token_enc, context=token_context(first.tenant_id))
        == "tok-teste"
    )
    assert route is not None and route.tenant_id == first.tenant_id
