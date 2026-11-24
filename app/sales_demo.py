"""Kit de demonstração para vendas: "Imobiliária Maré Alta" e "Pousada Recanto da Enseada".

Imobiliárias fictícias (`is_demo`) com números `demo-…` que nunca saem para o WhatsApp
(o `DemoGateway` grava os envios no Redis). Plantão 24 h e um corretor fictício com
consentimento, para a passagem "responda 1" aparecer na tela do corretor. Os contatos
dessas imobiliárias são apagados todo dia (`purge_sales_demos`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from redis.asyncio import Redis
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.demo_gateway import DemoOutbox
from app.core.config import TIMEZONE
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import global_session, tenant_session
from app.demo import connect_number, existing_tenant, load_catalog, season_year
from app.importer.sales_demo import SALES_DEMOS, DemoAgency
from app.models import AppUser, Consent, Contact, Tenant, TenantMember
from app.models.enums import ConsentPurpose, MemberRole
from app.onboarding import create_tenant, slugify

ALL_DAY: Final = {str(day): [["00:00", "23:59"]] for day in range(7)}
DEMO_CHANNEL_TOKEN: Final = "demo-sem-envio"  # noqa: S105 - não é segredo: o DemoGateway não envia
"""O canal exige token cifrado; o dos números `demo-` nunca é usado."""
PURGE_HOUR: Final = 3
log = get_logger(__name__)


@dataclass(frozen=True)
class SalesDemoResult:
    name: str
    slug: str
    tenant_id: uuid.UUID
    created: bool
    snapshot_version: int


def gestor_email(agency: DemoAgency) -> str:
    return f"gestor-{agency.pnid}@rentaldesk.invalid"


def broker_email(agency: DemoAgency) -> str:
    return f"corretor-{agency.pnid}@rentaldesk.invalid"


async def seed_sales_demos(
    ops: async_sessionmaker[AsyncSession],
    app: async_sessionmaker[AsyncSession],
    keyring: Keyring,
    *,
    now: datetime,
) -> list[SalesDemoResult]:
    return [await _seed_one(ops, app, keyring, agency, now) for agency in SALES_DEMOS]


async def _seed_one(
    ops: async_sessionmaker[AsyncSession], app: async_sessionmaker[AsyncSession],
    keyring: Keyring, agency: DemoAgency, now: datetime,
) -> SalesDemoResult:  # fmt: skip
    slug = slugify(agency.name)
    tenant_id, created = await existing_tenant(app, slug), False
    if tenant_id is None:
        new = await create_tenant(
            ops, app, name=agency.name, gestor_email=gestor_email(agency),
            gestor_name="Gestor fictício", is_demo=True,
        )  # fmt: skip
        tenant_id, slug, created = new.tenant_id, new.slug, True
    version = await load_catalog(
        app, tenant_id, gestor_email(agency), agency.catalog(season_year(now.date())),
        now=now, file_name=f"{agency.pnid}.xlsx",
    )  # fmt: skip
    async with tenant_session(app, tenant_id) as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(broker_hours=ALL_DAY)
        )
        await connect_number(
            session, keyring, tenant_id, agency.pnid, DEMO_CHANNEL_TOKEN,
            display_phone="demonstração",
        )  # fmt: skip
    await _ensure_broker(app, tenant_id, agency, now)
    return SalesDemoResult(agency.name, slug, tenant_id, created, version)


async def _ensure_broker(
    app: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, agency: DemoAgency,
    now: datetime,
) -> None:  # fmt: skip
    email = broker_email(agency)
    async with global_session(app) as session:
        user_id = await session.scalar(select(AppUser.id).where(AppUser.email == email))
        if user_id is None:
            user_id = uuid.uuid4()
            # Sem senha: o corretor fictício não entra no painel.
            session.add(AppUser(id=user_id, email=email, display_name=agency.broker_name))
    async with tenant_session(app, tenant_id) as session:
        member = await session.scalar(select(TenantMember).where(TenantMember.user_id == user_id))
        if member is None:
            session.add(
                TenantMember(tenant_id=tenant_id, user_id=user_id, role=MemberRole.CORRETOR,
                             whatsapp_phone=agency.broker_phone)
            )  # fmt: skip
        consent = await session.scalar(
            select(Consent.id).where(
                Consent.user_id == user_id, Consent.purpose == ConsentPurpose.HANDOFF_ALERT,
                Consent.revoked_at.is_(None),
            )
        )  # fmt: skip
        if consent is None:
            session.add(
                Consent(tenant_id=tenant_id, user_id=user_id, phone_e164=agency.broker_phone,
                        purpose=ConsentPurpose.HANDOFF_ALERT, granted_at=now,
                        evidence="corretor fictício do kit de demonstração")
            )  # fmt: skip


async def demo_tenants(ops_engine: AsyncEngine) -> list[uuid.UUID]:
    """Imobiliárias de demonstração (consulta global, só no scheduler)."""
    async with ops_engine.connect() as conn:
        rows = await conn.execute(text("SELECT id FROM tenant WHERE is_demo"))
    return [row[0] for row in rows]


def purge_due(now: datetime) -> bool:
    """Madrugada no horário de Brasília, quando ninguém está demonstrando."""
    return now.astimezone(TIMEZONE).hour >= PURGE_HOUR


async def purge_sales_demos(
    redis: Redis, ops_engine: AsyncEngine, app: async_sessionmaker[AsyncSession], *,
    now: datetime,
) -> int:  # fmt: skip
    """Uma vez por dia: apaga visitantes e conversas das imobiliárias demo e as telas no Redis."""
    if not purge_due(now):
        return 0
    day = now.astimezone(TIMEZONE).date().isoformat()
    if not await redis.set(f"rd:demo:purge:{day}", "1", nx=True, ex=2 * 86400):
        return 0
    deleted = 0
    for tenant_id in await demo_tenants(ops_engine):
        deleted += await purge_contacts(app, tenant_id, now)
    screens = await DemoOutbox(redis).clear_all()
    log.info("demo_limpa", contatos=deleted, telas=screens)
    return deleted


async def purge_contacts(
    app: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, now: datetime
) -> int:
    """Apaga os visitantes da demo (contatos, conversas e mensagens em cascata) e renova as
    datas: numa demonstração ninguém confirma as datas às 9h, e sem isso, depois de 24 h, os
    cartões passariam a dizer que a disponibilidade não foi atualizada."""
    async with tenant_session(app, tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_demo:
            return 0
        tenant.availability_updated_at = now
        deleted = await session.scalars(
            delete(Contact).where(Contact.tenant_id == tenant_id).returning(Contact.id)
        )
        return len(deleted.all())
