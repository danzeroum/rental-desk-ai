# ruff: noqa: F811 - as fixtures `world`, `redis` e `keyring` vêm de test_handoffs
"""Relatório semanal (RF18): agregados sem dado pessoal, gravados uma vez e enviados ao gestor."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.mailer import FakeMailer
from app.core.config import TIMEZONE
from app.core.rls import global_session, tenant_session
from app.models import AppUser, WeeklyReport
from app.models.enums import MemberRole
from app.scheduler.app import send_weekly_reports
from app.workers.reports import ReportService, offered_totals, report_due
from tests.integration.test_handoffs import (  # noqa: F401 - fixtures
    Sessions,
    World,
    add_member,
    keyring,
    redis,
    world,
)

pytestmark = pytest.mark.integration
NEXT_MONDAY_9H = datetime(2026, 11, 30, 9, 0, tzinfo=TIMEZONE)


def test_offered_totals_tolerates_odd_payloads() -> None:
    assert offered_totals({"search": {"results": [{"total_cents": 10}, {"x": 1}, "y"]}}) == [10]
    assert offered_totals({"search": None}) == [] and offered_totals({}) == []


def test_due_only_on_monday_morning() -> None:
    assert report_due(NEXT_MONDAY_9H)
    assert not report_due(datetime(2026, 11, 30, 7, 59, tzinfo=TIMEZONE))
    assert not report_due(datetime(2026, 12, 1, 9, 0, tzinfo=TIMEZONE))


async def _gestor_with_password(world: World) -> str:
    user_id = await add_member(world.app, world.tenant, "Gil", "+5513988880001", MemberRole.GESTOR)
    async with global_session(world.app) as session:
        await session.execute(
            update(AppUser).where(AppUser.id == user_id).values(password_hash="argon2-de-teste")
        )
        email = await session.scalar(select(AppUser.email).where(AppUser.id == user_id))
    assert email
    return email


async def test_weekly_report(world: World, ops_engine: AsyncEngine) -> None:
    email = await _gestor_with_password(world)
    await add_member(world.app, world.tenant, "Bia", "+5513988880002", MemberRole.CORRETOR)
    # O banco carimba `created_at` com a hora real: o relógio do teste acompanha.
    world.clock.now = datetime.now(TIMEZONE)
    await world.say("Quero uma casa para 6 pessoas de 10/12 a 15/12")
    await world.say("Quero falar com um corretor", sender=f"5511{uuid.uuid4().int % 10**9:09d}")
    today = world.clock.now.date()
    world.clock.now = datetime.combine(
        today + timedelta(days=7 - today.weekday()), time(9), tzinfo=TIMEZONE
    )
    service = ReportService(
        sessions=world.app, mailer=world.mailer, public_base_url="https://painel", clock=world.clock
    )
    metrics = await service.weekly(world.tenant)
    assert metrics is not None
    assert (metrics.contacts, metrics.handoffs, metrics.without_outcome) == (2, 1, 1)
    assert metrics.offered_value_cents > 0 and metrics.first_response_median_s is not None
    assert await service.weekly(world.tenant) is None  # uma vez por semana
    [mail] = [m for m in world.mailer.sent if m.to == email]
    assert "Contatos atendidos: 2" in mail.body and "show=sem-desfecho" in mail.body
    assert "+55" not in mail.body  # sem telefone de hóspede
    async with tenant_session(world.app, world.tenant) as session:
        stored = await session.scalar(select(WeeklyReport))
    assert stored is not None and stored.metrics["contacts"] == 2
    assert stored.period_start == today - timedelta(days=today.weekday())


async def test_scheduler_waits_for_monday(ops_engine: AsyncEngine, app_sessions: Sessions) -> None:
    tuesday = datetime(2026, 12, 1, 9, 0, tzinfo=TIMEZONE)
    service = ReportService(
        sessions=app_sessions,
        mailer=FakeMailer(),
        public_base_url="https://p",
        clock=lambda: tuesday,
    )
    assert await send_weekly_reports(ops_engine, service) == 0
