"""Modo demonstração (RF25): "Imobiliária Demo Riviera" com 10 casas fictícias.

`make seed-demo` cria (ou renova) a imobiliária de demonstração: dados da temporada
corrente aprovados, imobiliária ativa e marcada `is_demo` (não recebe lembrete nem
relatório, e o painel avisa que os dados são fictícios). Com o número de teste da Meta,
o assistente atende de verdade: `make seed-demo PNID=<phone_number_id>` com o token na
variável `DEMO_WHATSAPP_TOKEN` do shell (nunca em argumento nem no código).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.link_checker import FakeLinkChecker
from app.core.crypto import Keyring
from app.core.rls import global_session, tenant_session
from app.importer.sample import DEMO_TENANT_NAME, build_sample
from app.importer.service import approve_snapshot, prepare_import, save_draft
from app.importer.workbook import build_workbook
from app.models import AppUser, Tenant, TenantRoute, WhatsAppAccount
from app.models.enums import TenantStatus
from app.onboarding import SLUG_CHANNEL, create_tenant, slugify
from app.workers.channel import seal_token

DEMO_GESTOR_EMAIL = "gestor-demo@rentaldesk.invalid"
"""Domínio reservado (.invalid): ninguém recebe e-mail; o operador entra como suporte."""
WHATSAPP_CHANNEL = "whatsapp"


class DemoError(Exception):
    pass


@dataclass(frozen=True)
class DemoResult:
    tenant_id: uuid.UUID
    slug: str
    created: bool
    snapshot_version: int
    phone_connected: bool


def season_year(today: date) -> int:
    """Temporada de dezembro a março: em janeiro-março ainda vale a do ano anterior."""
    return today.year if today.month >= 4 else today.year - 1  # noqa: PLR2004


async def _existing(app: async_sessionmaker[AsyncSession], slug: str) -> uuid.UUID | None:
    async with global_session(app) as session:
        tenant_id: uuid.UUID | None = await session.scalar(
            select(TenantRoute.tenant_id).where(
                TenantRoute.channel == SLUG_CHANNEL, TenantRoute.external_id == slug
            )
        )
    return tenant_id


async def seed_demo(
    ops: async_sessionmaker[AsyncSession],
    app: async_sessionmaker[AsyncSession],
    keyring: Keyring,
    *,
    now: datetime,
    phone_number_id: str | None = None,
    access_token: str | None = None,
) -> DemoResult:
    if phone_number_id and not access_token:
        raise DemoError("defina DEMO_WHATSAPP_TOKEN no shell para ligar o número de teste")
    slug = slugify(DEMO_TENANT_NAME)
    tenant_id, created = await _existing(app, slug), False
    if tenant_id is None:
        new = await create_tenant(
            ops, app, name=DEMO_TENANT_NAME, gestor_email=DEMO_GESTOR_EMAIL,
            gestor_name="Gestor da demonstração", is_demo=True,
        )  # fmt: skip
        tenant_id, slug, created = new.tenant_id, new.slug, True
    report = await prepare_import(
        build_workbook(build_sample(season_year(now.date()))),
        today=now.date(), link_checker=FakeLinkChecker(),
    )  # fmt: skip
    if not report.ok:  # pragma: no cover - a amostra é validada nos testes
        raise DemoError("a planilha de demonstração não passou na validação")
    async with global_session(app) as session:
        gestor_id = await session.scalar(
            select(AppUser.id).where(AppUser.email == DEMO_GESTOR_EMAIL)
        )
    if gestor_id is None:  # pragma: no cover - criado junto com a imobiliária
        raise DemoError("gestor da demonstração não encontrado")
    async with tenant_session(app, tenant_id, user_id=gestor_id) as session:
        draft = await save_draft(
            session, tenant_id=tenant_id, user_id=gestor_id, report=report,
            file_name="demo-riviera.xlsx",
        )  # fmt: skip
    async with tenant_session(app, tenant_id, user_id=gestor_id) as session:
        snapshot = await approve_snapshot(
            session, tenant_id=tenant_id, snapshot_id=draft.id, user_id=gestor_id, now=now
        )
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(status=TenantStatus.ACTIVE)
        )
        if phone_number_id and access_token:
            await _connect_number(session, keyring, tenant_id, phone_number_id, access_token)
    return DemoResult(tenant_id, slug, created, snapshot.version, bool(phone_number_id))


async def _connect_number(
    session: AsyncSession, keyring: Keyring, tenant_id: uuid.UUID, pnid: str, token: str
) -> None:
    sealed = seal_token(keyring, tenant_id, token)
    account = await session.scalar(
        select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id == pnid)
    )
    if account is None:
        session.add(
            WhatsAppAccount(
                tenant_id=tenant_id, phone_number_id=pnid, display_phone="teste da Meta",
                access_token_enc=sealed, is_test_number=True,
            )
        )  # fmt: skip
        session.add(TenantRoute(channel=WHATSAPP_CHANNEL, external_id=pnid, tenant_id=tenant_id))
    else:
        account.access_token_enc = sealed
