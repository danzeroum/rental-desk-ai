"""Comandos de administração (rodam dentro do contêiner `app`).

    python -m app.cli create-operator --email ana@exemplo.com --name "Ana"
    python -m app.cli reset-totp --email ana@exemplo.com
    python -m app.cli create-tenant --name "Imobiliária Sol" --gestor-email gestor@sol.com

`create-tenant` precisa de `DATABASE_OPS_URL` (só no contêiner do scheduler):
inserir imobiliária é tarefa global do operador, fora do caminho de requisição.

`create-operator` cria (ou promove) o usuário operador e imprime o link de convite,
válido por 72 h — também enviado por e-mail quando há SMTP. `reset-totp` apaga o
autenticador de quem perdeu o celular e encerra as sessões; no próximo login a
pessoa cadastra um novo.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.factory import build_keyring, build_mailer
from app.core.config import get_settings
from app.core.db import async_url, create_engine
from app.core.logging import configure_logging
from app.core.redis import create_redis
from app.core.rls import global_session, scoped_session_factory
from app.models import AppUser
from app.onboarding import OnboardingError, create_tenant
from app.web.auth import AuthService


class CliError(Exception):
    pass


@asynccontextmanager
async def auth_service() -> AsyncIterator[AuthService]:
    settings = get_settings()
    configure_logging("WARNING")
    engine, redis = create_engine(settings), create_redis(settings)
    try:
        yield AuthService(
            sessions=scoped_session_factory(engine),
            redis=redis,
            keyring=build_keyring(settings),
            mailer=build_mailer(settings),
            public_base_url=settings.public_base_url,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


def _normalize(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise CliError(f"e-mail inválido: {email!r}")
    return cleaned


async def create_operator(service: AuthService, email: str, name: str) -> str:
    email = _normalize(email)
    async with global_session(service.db) as session:
        user = await session.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(email=email, display_name=name.strip() or email, is_operator=True)
            session.add(user)
            await session.flush()
        user.is_operator, user.is_active = True, True
    return await service.send_invite(user, None)


async def reset_totp(service: AuthService, email: str) -> None:
    async with global_session(service.db) as session:
        user = await session.scalar(select(AppUser).where(AppUser.email == _normalize(email)))
        if user is None:
            raise CliError("usuário não encontrado")
        user.totp_secret_enc = None
        user_id = user.id
    await service.end_all_sessions(user_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    operator = commands.add_parser("create-operator", help="cria o operador e imprime o convite")
    operator.add_argument("--email", required=True)
    operator.add_argument("--name", default="Operador")
    tenant = commands.add_parser("create-tenant", help="cria a imobiliária e convida o gestor")
    tenant.add_argument("--name", required=True)
    tenant.add_argument("--gestor-email", required=True)
    tenant.add_argument("--gestor-name", default="Gestor")
    totp = commands.add_parser("reset-totp", help="apaga o autenticador de um usuário")
    totp.add_argument("--email", required=True)
    return parser


async def provision_tenant(
    service: AuthService, ops_url: str, name: str, email: str, gestor: str
) -> str:
    engine = create_async_engine(async_url(ops_url), poolclass=NullPool)
    try:
        created = await create_tenant(
            async_sessionmaker(engine, expire_on_commit=False), service.db,
            name=name, gestor_email=email, gestor_name=gestor,
        )  # fmt: skip
    except OnboardingError as exc:
        raise CliError(str(exc)) from exc
    finally:
        await engine.dispose()
    async with global_session(service.db) as session:
        user = await session.get(AppUser, created.gestor_id)
    if user is None:  # pragma: no cover - acabou de ser criado
        raise CliError("gestor não encontrado")
    if created.gestor_has_password:
        await service.notify_added(user, name)
        return f"Imobiliária {name} criada ({created.slug}); o gestor já tinha conta e foi avisado."
    link = await service.send_invite(user, name)
    return f"Imobiliária {name} criada ({created.slug}). Convite do gestor (vale 72 h):\n{link}"


async def _run(args: argparse.Namespace) -> str:
    async with auth_service() as service:
        if args.command == "create-tenant":
            ops_url = get_settings().database_ops_url
            if ops_url is None:
                raise CliError("rode no contêiner do scheduler (precisa de DATABASE_OPS_URL)")
            return await provision_tenant(
                service, str(ops_url), args.name, args.gestor_email, args.gestor_name
            )
        if args.command == "create-operator":
            link = await create_operator(service, args.email, args.name)
            return f"Operador pronto. Convite (vale 72 h):\n{link}"
        await reset_totp(service, args.email)
        return "Autenticador apagado; no próximo login a pessoa cadastra um novo."


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(asyncio.run(_run(args)))  # noqa: T201 - saída do comando
    except CliError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
