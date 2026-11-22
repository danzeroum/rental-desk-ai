# ruff: noqa: F811 - as fixtures `redis` e `keyring` vêm de test_pipeline
"""Calendários iCal (RF26): bloqueios do Airbnb em até 15 min, falhas e validade das datas."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select, update

from app.adapters.fake_llm import FakeLLM
from app.adapters.http_fetch import FakeFetcher, FetchError
from app.adapters.mailer import FakeMailer
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.models import Block, CalendarFeed, Property, Tenant
from app.models.enums import BlockSource, MemberRole
from app.scheduler.app import run_scheduler  # noqa: F401 - módulo importável
from app.workers.calendars import SYNC_EVERY_SECONDS, CalendarService, FeedError, describe
from tests.integration.test_handoffs import add_member
from tests.integration.test_pipeline import (  # noqa: F401 - fixtures
    NOW,
    Sessions,
    World,
    keyring,
    redis,
    setup_tenant,
)

pytestmark = pytest.mark.integration
URL = "https://www.airbnb.com.br/calendar/ical/123456.ics?s=token-secreto-do-anfitriao"


def ics(*periods: tuple[str, str, str]) -> bytes:
    events = "".join(
        f"BEGIN:VEVENT\r\nUID:{uid}@airbnb.com\r\nDTSTART;VALUE=DATE:{start}\r\nDTEND;VALUE=DATE:{end}\r\nEND:VEVENT\r\n"
        for uid, start, end in periods
    )
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{events}END:VCALENDAR\r\n".encode()


def service(
    app: Sessions, keyring: Keyring, fetcher: FakeFetcher, mailer: FakeMailer
) -> CalendarService:
    return CalendarService(
        sessions=app, fetcher=fetcher, keyring=keyring, mailer=mailer,
        public_base_url="https://painel", clock=lambda: NOW,
    )  # fmt: skip


def test_describe_hides_token() -> None:
    assert describe(URL) == ("Airbnb", "www.airbnb.com.br")
    assert describe("https://admin.booking.com/hotel/ical.ics?t=x")[0] == "Booking"
    with pytest.raises(FeedError):
        describe("airbnb")


async def test_airbnb_booking_reaches_search_and_updates(
    app_sessions: Sessions, ops_sessions: Sessions, redis: Redis, keyring: Keyring
) -> None:
    assert SYNC_EVERY_SECONDS == 15 * 60
    tenant_id, pnid = await setup_tenant(app_sessions, ops_sessions, keyring)
    fetcher, mailer = FakeFetcher({URL: ics(("a", "20261210", "20261215"))}), FakeMailer()
    calendars = service(app_sessions, keyring, fetcher, mailer)
    async with tenant_session(app_sessions, tenant_id) as session:
        feed = await calendars.add(session, tenant_id, None, "CASA08", URL)
        feed_id = feed.id
        assert b"token-secreto" not in feed.url_enc
    world = World(app_sessions, redis, keyring, FakeLLM())
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert "(CASA08)" in world.gateway.texts[-1].body  # ainda livre
    assert await calendars.sync_tenant(tenant_id) == 1
    await world.say(pnid, "Casa para 4 de 10/12 a 15/12")
    assert "(CASA08)" not in world.gateway.texts[-1].body  # reserva do Airbnb bloqueou

    fetcher.pages[URL] = ics(("b", "20261228", "20270102"))  # reserva cancelada, outra criada
    await calendars.sync_tenant(tenant_id)
    async with tenant_session(app_sessions, tenant_id) as session:
        blocks = list(
            (
                await session.execute(select(Block).where(Block.calendar_feed_id == feed_id))
            ).scalars()
        )
    assert [(b.start_date, b.end_date, b.source) for b in blocks] == [
        (date(2026, 12, 28), date(2027, 1, 2), BlockSource.ICAL)
    ]
    assert blocks[0].external_uid == f"{feed_id}:b@airbnb.com"


async def test_three_failures_alert_once_and_success_resets(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring)
    await add_member(app_sessions, tenant_id, "Gil", "+5513988771100", MemberRole.GESTOR)
    fetcher, mailer = (
        FakeFetcher({URL: FetchError("o endereço respondeu com erro 410")}),
        FakeMailer(),
    )
    calendars = service(app_sessions, keyring, fetcher, mailer)
    async with tenant_session(app_sessions, tenant_id) as session:
        feed_id = (await calendars.add(session, tenant_id, None, "CASA01", URL)).id
    for _ in range(4):
        assert not await calendars.sync_feed(tenant_id, feed_id)
    assert len(mailer.sent) == 1 and "falhou 3 vezes" in mailer.sent[0].body
    assert "token-secreto" not in mailer.sent[0].body
    fetcher.pages[URL] = ics()
    assert await calendars.sync_feed(tenant_id, feed_id)
    async with tenant_session(app_sessions, tenant_id) as session:
        feed = await session.get(CalendarFeed, feed_id)
    assert feed is not None and feed.consecutive_failures == 0 and feed.alerted_at is None


async def test_freshness_only_when_every_house_is_covered(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring)
    stale = NOW - timedelta(days=3)
    async with tenant_session(app_sessions, tenant_id) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(availability_updated_at=stale)
        )
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        codes = list(
            (
                await session.execute(
                    select(Property.code).where(Property.snapshot_id == tenant.active_snapshot_id)
                )
            ).scalars()
        )
    fetcher = FakeFetcher({f"{URL}&c={c}": ics() for c in codes})
    calendars = service(app_sessions, keyring, fetcher, FakeMailer())
    async with tenant_session(app_sessions, tenant_id) as session:
        await calendars.add(session, tenant_id, None, codes[0], f"{URL}&c={codes[0]}")
    await calendars.sync_tenant(tenant_id)
    async with tenant_session(app_sessions, tenant_id) as session:
        assert (await session.get(Tenant, tenant_id)).availability_updated_at == stale  # type: ignore[union-attr]
        for code in codes[1:]:
            await calendars.add(session, tenant_id, None, code, f"{URL}&c={code}")
    await calendars.sync_tenant(tenant_id)
    async with tenant_session(app_sessions, tenant_id) as session:
        assert (await session.get(Tenant, tenant_id)).availability_updated_at == NOW  # type: ignore[union-attr]


async def test_unknown_house_is_refused(
    app_sessions: Sessions, ops_sessions: Sessions, keyring: Keyring
) -> None:
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring)
    calendars = service(app_sessions, keyring, FakeFetcher(), FakeMailer())
    with pytest.raises(FeedError, match="CASA99"):
        async with tenant_session(app_sessions, tenant_id) as session:
            await calendars.add(session, tenant_id, None, "CASA99", URL)
