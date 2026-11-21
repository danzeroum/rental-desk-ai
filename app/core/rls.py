"""Sessões de banco com escopo de tenant (RLS + filtro na aplicação).

Defesa em profundidade (SPEC, seção 6):

1. **RLS no Postgres:** toda transação de uma sessão com tenant executa
   `set_config('app.current_tenant_id', <id>, true)` (equivale a `SET LOCAL`), e as
   políticas comparam `tenant_id` com esse valor. Sem tenant definido, as tabelas
   de negócio devolvem zero linhas (falha fechada).
2. **Filtro na aplicação:** toda consulta ORM ganha `tenant_id = <id>` para os
   modelos `TenantScoped`, mesmo que o código esqueça o `where`.

O valor é reaplicado a cada nova transação da sessão (evento `after_begin`), então
um `commit()` no meio do bloco não deixa a sessão sem escopo.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction, with_loader_criteria

from app.core.logging import bind_context
from app.models.base import TenantScoped

TENANT_KEY: Final = "tenant_id"
USER_KEY: Final = "user_id"
_SET_CONFIG = text("SELECT set_config(:name, :value, true)")


class ScopedSession(Session):
    """Sessão síncrona subjacente; carrega o escopo em `Session.info`."""


@event.listens_for(ScopedSession, "after_begin")
def _apply_scope(session: Session, _: SessionTransaction, connection: Connection) -> None:
    for key, setting in ((TENANT_KEY, "app.current_tenant_id"), (USER_KEY, "app.current_user_id")):
        value = session.info.get(key)
        connection.execute(_SET_CONFIG, {"name": setting, "value": str(value) if value else ""})


@event.listens_for(ScopedSession, "do_orm_execute")
def _filter_by_tenant(state: ORMExecuteState) -> None:
    tenant_id = state.session.info.get(TENANT_KEY)
    if (
        tenant_id is None
        or not state.is_select
        or state.is_column_load
        or state.is_relationship_load
    ):
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
            track_closure_variables=False,
        )
    )


def scoped_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=ScopedSession)


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Transação com escopo do tenant; confirma ao sair sem erro, desfaz com erro."""
    info: dict[str, Any] = {TENANT_KEY: tenant_id, USER_KEY: user_id}
    async with factory(info=info) as session, session.begin():
        bind_context(tenant_id=str(tenant_id))
        yield session


@asynccontextmanager
async def global_session(
    factory: async_sessionmaker[AsyncSession], *, user_id: uuid.UUID | None = None
) -> AsyncIterator[AsyncSession]:
    """Transação sem tenant: só tabelas globais (login, roteamento do webhook).

    As tabelas de negócio aparecem vazias aqui, por causa do RLS.
    """
    async with factory(info={TENANT_KEY: None, USER_KEY: user_id}) as session, session.begin():
        yield session
