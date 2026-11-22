"""Contexto de uma requisição do painel dentro de uma imobiliária.

A imobiliária vem da sessão (escolhida no login, com o vínculo conferido); toda
consulta usa `tenant_session`, com RLS. Papéis: gestor e operador (suporte)
administram; corretor vê o início, as passagens e os bloqueios.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rls import tenant_session
from app.models.enums import MemberRole
from app.web.auth import PanelSession
from app.web.common import panel_session

OPERATOR_ROLE: Final = "operador"
MANAGER_ROLES: Final = frozenset({MemberRole.GESTOR.value, OPERATOR_ROLE})


@dataclass
class TenantContext:
    request: Request
    panel: PanelSession
    tenant_id: uuid.UUID

    @property
    def user_id(self) -> uuid.UUID:
        return self.panel.user_uuid

    @property
    def is_manager(self) -> bool:
        return self.panel.role in MANAGER_ROLES

    def now(self) -> datetime:
        clock = getattr(self.request.app.state.auth, "clock", None)
        return clock() if clock else datetime.now(UTC)

    @asynccontextmanager
    async def db(self) -> AsyncIterator[AsyncSession]:
        async with tenant_session(
            self.request.app.state.sessions, self.tenant_id, user_id=self.user_id
        ) as session:
            yield session


ActivePanel = Annotated[PanelSession, Depends(panel_session)]


async def tenant_context(request: Request, panel: ActivePanel) -> TenantContext:
    if panel.tenant_uuid is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/painel/escolher"})
    return TenantContext(request, panel, panel.tenant_uuid)


TenantCtx = Annotated[TenantContext, Depends(tenant_context)]


async def manager_context(ctx: TenantCtx) -> TenantContext:
    if not ctx.is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Só o gestor pode fazer isso.")
    return ctx


ManagerCtx = Annotated[TenantContext, Depends(manager_context)]
