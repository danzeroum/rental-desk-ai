"""Criação de imobiliária (tarefa global do operador, SPEC seção 6).

Só o papel `rentaldesk_ops` insere em `tenant`; por isso roda por comando no
contêiner do scheduler (`make tenant`), nunca no caminho de requisição. O resto
(rota do link de privacidade, aviso v1, gestor) entra com o papel da aplicação e RLS.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import append_audit
from app.core.rls import global_session, tenant_session
from app.domain.privacy import default_notice, notice_hash
from app.models import AppUser, PrivacyNotice, Tenant, TenantMember, TenantRoute
from app.models.enums import MemberRole, TenantStatus

SLUG_CHANNEL = "slug"
"""`tenant_route` com o slug: o link do aviso de privacidade acha a imobiliária sem RLS global."""


class OnboardingError(Exception):
    pass


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:50].strip("-")
    if not slug:
        raise OnboardingError("nome sem letras ou números")
    return slug


@dataclass(frozen=True)
class NewTenant:
    tenant_id: uuid.UUID
    slug: str
    gestor_id: uuid.UUID
    gestor_has_password: bool


async def _free_slug(ops: async_sessionmaker[AsyncSession], base: str) -> str:
    async with ops() as session:
        taken = set(
            (
                await session.execute(select(Tenant.slug).where(Tenant.slug.like(f"{base}%")))
            ).scalars()
        )
    if base not in taken:
        return base
    return next(f"{base}-{n}" for n in range(2, 10_000) if f"{base}-{n}" not in taken)


async def create_tenant(
    ops: async_sessionmaker[AsyncSession],
    app: async_sessionmaker[AsyncSession],
    *,
    name: str,
    gestor_email: str,
    gestor_name: str,
    is_demo: bool = False,
) -> NewTenant:
    name, email = name.strip(), gestor_email.strip().lower()
    if not name or len(name) > 120:  # noqa: PLR2004
        raise OnboardingError("o nome precisa ter de 1 a 120 caracteres")
    if "@" not in email:
        raise OnboardingError(f"e-mail inválido: {gestor_email!r}")
    slug = await _free_slug(ops, slugify(name))
    tenant_id = uuid.uuid4()
    async with ops() as session, session.begin():
        session.add(
            Tenant(
                id=tenant_id, name=name, slug=slug, status=TenantStatus.ONBOARDING, is_demo=is_demo
            )
        )
    async with global_session(app) as session:
        user = await session.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(email=email, display_name=gestor_name.strip() or email)
            session.add(user)
            await session.flush()
        gestor_id, has_password = user.id, bool(user.password_hash)
    content = default_notice(name)
    async with tenant_session(app, tenant_id) as session:
        session.add(TenantRoute(channel=SLUG_CHANNEL, external_id=slug, tenant_id=tenant_id))
        session.add(
            PrivacyNotice(
                tenant_id=tenant_id, version=1, content=content, content_hash=notice_hash(content)
            )
        )
        session.add(TenantMember(tenant_id=tenant_id, user_id=gestor_id, role=MemberRole.GESTOR))
        await append_audit(
            session, tenant_id=tenant_id, event_type="tenant_created",
            payload={"slug": slug, "gestor_id": str(gestor_id), "is_demo": is_demo},
        )  # fmt: skip
    return NewTenant(tenant_id, slug, gestor_id, has_password)
