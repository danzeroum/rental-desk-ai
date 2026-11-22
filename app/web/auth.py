"""Autenticação do painel: senha + TOTP obrigatório, sessões, convite e recuperação.

Sessões vivem no Redis (`rd:sess:<sha256 do token>`); o cookie só carrega o token
aleatório. Estágios: `awaiting_totp` (senha conferida, falta TOTP), `totp_setup`
(primeiro acesso: precisa cadastrar o TOTP) e `active`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.mailer import Mailer, MailError
from app.core.audit import append_audit
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import global_session, tenant_session
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    new_totp_secret,
    password_problem,
    verify_password,
    verify_totp,
)
from app.models import AppUser, Tenant, TenantMember

MAX_FAILED_LOGINS: Final = 5
MAX_TOTP_ATTEMPTS: Final = 5
LOCK_FOR: Final = timedelta(minutes=15)
IP_ATTEMPTS: Final = 30
IP_WINDOW_SECONDS: Final = 600
SESSION_IDLE_SECONDS: Final = 8 * 3600
INVITE_SECONDS: Final = 72 * 3600
RESET_SECONDS: Final = 3600
GENERIC_LOGIN_ERROR: Final = "E-mail ou senha incorretos."
_DUMMY_HASH: Final = hash_password("conta-inexistente-para-tempo-constante")

log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Stage(StrEnum):
    AWAITING_TOTP = "awaiting_totp"
    TOTP_SETUP = "totp_setup"
    ACTIVE = "active"


@dataclass
class PanelSession:
    user_id: str
    stage: str
    csrf: str
    tenant_id: str | None = None
    pending_totp_secret: str | None = None
    is_operator: bool = False
    role: str | None = None
    """Papel na imobiliária escolhida: gestor, corretor ou operador (suporte)."""
    tenant_name: str | None = None

    @property
    def user_uuid(self) -> uuid.UUID:
        return uuid.UUID(self.user_id)

    @property
    def tenant_uuid(self) -> uuid.UUID | None:
        return uuid.UUID(self.tenant_id) if self.tenant_id else None


class AuthError(Exception):
    """Mensagem pronta para a pessoa."""


def totp_context(user_id: uuid.UUID) -> str:
    return f"totp:{user_id}"


class AuthService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        keyring: Keyring,
        mailer: Mailer,
        public_base_url: str,
        clock: Clock = _utcnow,
    ) -> None:
        self.db, self.redis, self.keyring, self.mailer = sessions, redis, keyring, mailer
        self.base_url = public_base_url.rstrip("/")
        self.clock = clock

    # --- Sessões --------------------------------------------------------------------

    async def create_session(self, user: AppUser, stage: Stage) -> str:
        token = new_token()
        data = PanelSession(str(user.id), stage, new_token(), is_operator=user.is_operator)
        await self._save(token, data)
        await self.redis.sadd(f"rd:user-sess:{user.id}", hash_token(token))
        return token

    async def _save(self, token: str, data: PanelSession) -> None:
        await self.redis.set(
            f"rd:sess:{hash_token(token)}", json.dumps(asdict(data)), ex=SESSION_IDLE_SECONDS
        )

    async def get_session(self, token: str | None) -> PanelSession | None:
        if not token:
            return None
        key = f"rd:sess:{hash_token(token)}"
        raw = await self.redis.get(key)
        if raw is None:
            return None
        await self.redis.expire(key, SESSION_IDLE_SECONDS)
        return PanelSession(**json.loads(raw))

    async def _require_session(self, token: str) -> PanelSession:
        data = await self.get_session(token)
        if data is None:
            raise AuthError("Sessão expirada. Entre de novo.")
        return data

    async def update_session(self, token: str, data: PanelSession) -> None:
        await self._save(token, data)

    async def end_session(self, token: str) -> None:
        await self.redis.delete(f"rd:sess:{hash_token(token)}")

    async def end_all_sessions(self, user_id: uuid.UUID) -> None:
        members: Any = await self.redis.smembers(f"rd:user-sess:{user_id}")
        for token_hash in members:
            await self.redis.delete(f"rd:sess:{token_hash}")
        await self.redis.delete(f"rd:user-sess:{user_id}")

    # --- Senha ----------------------------------------------------------------------

    async def login(self, email: str, password: str, ip: str) -> tuple[str, PanelSession]:
        """Confere a senha; devolve (token, sessão) no estágio de TOTP ou de cadastro de TOTP."""
        ip_key = f"rd:login-ip:{ip}"
        attempts = await self.redis.incr(ip_key)
        if attempts == 1:
            await self.redis.expire(ip_key, IP_WINDOW_SECONDS)
        if attempts > IP_ATTEMPTS:
            raise AuthError("Muitas tentativas deste endereço. Tente de novo em alguns minutos.")
        now = self.clock()
        error: str | None = None
        async with global_session(self.db) as session:
            user = await session.scalar(
                select(AppUser).where(AppUser.email == email.strip().lower())
            )
            if user is None or not user.is_active or not user.password_hash:
                verify_password(_DUMMY_HASH, password)  # tempo parecido exista ou não a conta
                raise AuthError(GENERIC_LOGIN_ERROR)
            if user.locked_until and user.locked_until > now:
                raise AuthError("Conta bloqueada por excesso de tentativas. Tente em 15 minutos.")
            if verify_password(user.password_hash, password):
                user.failed_logins, user.locked_until = 0, None
                stage = Stage.AWAITING_TOTP if user.totp_secret_enc else Stage.TOTP_SETUP
                token = await self.create_session(user, stage)
            else:
                error = GENERIC_LOGIN_ERROR
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILED_LOGINS:
                    user.locked_until, user.failed_logins = now + LOCK_FOR, 0
                    log.warning("conta_bloqueada_por_tentativas", user_id=str(user.id))
        if error:  # levantado fora da transação para gravar a contagem de falhas
            raise AuthError(error)
        return token, await self._require_session(token)

    # --- TOTP -----------------------------------------------------------------------

    async def check_totp(self, token: str, data: PanelSession, code: str) -> PanelSession:
        async with global_session(self.db) as session:
            user = await session.get(AppUser, data.user_uuid)
            if user is None or user.totp_secret_enc is None:
                raise AuthError("Cadastre o autenticador primeiro.")
            await self._limit_totp_attempts(user.id, token)
            secret = self.keyring.decrypt(user.totp_secret_enc, context=totp_context(user.id))
            step = verify_totp(secret, code, self.clock())
            if step is None or not await self.redis.set(
                f"rd:totp-used:{user.id}:{step}", "1", nx=True, ex=180
            ):
                raise AuthError("Código inválido ou já usado. Confira o horário do celular.")
            await self.redis.delete(f"rd:totp-fail:{user.id}")
            user.last_login_at = self.clock()
        data.stage = Stage.ACTIVE
        await self.update_session(token, data)
        return data

    async def _limit_totp_attempts(self, user_id: uuid.UUID, token: str) -> None:
        key = f"rd:totp-fail:{user_id}"
        failures = await self.redis.incr(key)
        if failures == 1:
            await self.redis.expire(key, int(LOCK_FOR.total_seconds()))
        if failures > MAX_TOTP_ATTEMPTS:
            await self.end_session(token)
            log.warning("totp_tentativas_excedidas", user_id=str(user_id))
            raise AuthError("Muitos códigos errados. Aguarde 15 minutos e entre de novo.")

    async def start_totp_setup(self, token: str, data: PanelSession) -> str:
        if data.pending_totp_secret is None:
            data.pending_totp_secret = new_totp_secret()
            await self.update_session(token, data)
        return data.pending_totp_secret

    async def confirm_totp_setup(self, token: str, data: PanelSession, code: str) -> PanelSession:
        secret = data.pending_totp_secret
        if secret is None or verify_totp(secret, code, self.clock()) is None:
            raise AuthError("Código não confere. Leia o QR de novo e digite o código atual.")
        async with global_session(self.db) as session:
            user = await session.get(AppUser, data.user_uuid)
            if user is None:
                raise AuthError("Usuário não encontrado.")
            user.totp_secret_enc = self.keyring.encrypt(secret, context=totp_context(user.id))
            user.last_login_at = self.clock()
        data.stage, data.pending_totp_secret = Stage.ACTIVE, None
        await self.update_session(token, data)
        log.info("totp_cadastrado")
        return data

    # --- Convite e recuperação --------------------------------------------------------

    async def invite_link(self, user_id: uuid.UUID) -> str:
        token = new_token()
        await self.redis.set(f"rd:invite:{hash_token(token)}", str(user_id), ex=INVITE_SECONDS)
        return f"{self.base_url}/convite/{token}"

    async def send_invite(self, user: AppUser, tenant_name: str | None) -> str:
        link = await self.invite_link(user.id)
        where = f" da {tenant_name}" if tenant_name else ""
        try:
            await self.mailer.send(
                user.email,
                "Convite para o painel",
                f"Olá, {user.display_name}!\n\nVocê foi convidado para o painel{where}. "
                f"Crie sua senha e cadastre o autenticador em até 72 horas:\n{link}\n",
            )
        except MailError:
            log.warning("convite_nao_enviado")
        return link

    async def notify_added(self, user: AppUser, tenant_name: str) -> None:
        """Quem já tem conta só recebe o aviso; entra com a senha e o autenticador de sempre."""
        try:
            await self.mailer.send(
                user.email,
                "Novo acesso no painel",
                f"Olá, {user.display_name}!\n\nVocê agora tem acesso à {tenant_name}. "
                f"Entre em {self.base_url}/login\n",
            )
        except MailError:
            log.warning("aviso_de_acesso_nao_enviado")

    async def accept_invite(self, token: str, password: str) -> tuple[str, PanelSession]:
        user_id = await self._redeem("invite", token, password, "Convite inválido ou vencido.")
        async with global_session(self.db) as session:
            user = await session.get(AppUser, user_id)
            if user is None:
                raise AuthError("Usuário não encontrado.")
            stage = Stage.AWAITING_TOTP if user.totp_secret_enc else Stage.TOTP_SETUP
            new = await self.create_session(user, stage)
        return new, await self._require_session(new)

    async def request_reset(self, email: str) -> None:
        """Sempre responde igual, exista o e-mail ou não."""
        async with global_session(self.db) as session:
            user = await session.scalar(
                select(AppUser).where(AppUser.email == email.strip().lower())
            )
        if user is None or not user.is_active:
            return
        token = new_token()
        await self.redis.set(f"rd:reset:{hash_token(token)}", str(user.id), ex=RESET_SECONDS)
        try:
            await self.mailer.send(
                user.email,
                "Nova senha do painel",
                "Para criar uma nova senha, abra em até 1 hora:\n"
                f"{self.base_url}/senha/nova/{token}\n"
                "Se não foi você, ignore este e-mail.",
            )
        except MailError:
            log.warning("email_recuperacao_nao_enviado")

    async def reset_password(self, token: str, password: str) -> None:
        user_id = await self._redeem("reset", token, password, "Link inválido ou vencido.")
        await self.end_all_sessions(user_id)

    async def _redeem(self, kind: str, token: str, password: str, invalid: str) -> uuid.UUID:
        """Confere a senha nova e só então consome o token (uso único, atômico)."""
        key = f"rd:{kind}:{hash_token(token)}"
        raw = await self.redis.get(key)
        if raw is None:
            raise AuthError(f"{invalid} Peça um novo.")
        user_id = uuid.UUID(str(raw))
        await self._set_password(user_id, password, commit=False)
        if await self.redis.getdel(key) is None:
            raise AuthError(f"{invalid} Peça um novo.")
        await self._set_password(user_id, password)
        return user_id

    async def _set_password(
        self, user_id: uuid.UUID, password: str, *, commit: bool = True
    ) -> None:
        """Valida a senha; com `commit`, grava o hash e zera o bloqueio."""
        async with global_session(self.db) as session:
            user = await session.get(AppUser, user_id)
            if user is None or not user.is_active:
                raise AuthError("Usuário não encontrado.")
            problem = password_problem(password, email=user.email)
            if problem:
                raise AuthError(f"Senha não aceita: {problem}.")
            if not commit:
                return
            user.password_hash = hash_password(password)
            user.failed_logins, user.locked_until = 0, None

    async def email_of(self, user_id: uuid.UUID) -> str:
        async with global_session(self.db) as session:
            user = await session.get(AppUser, user_id)
            return user.email if user else ""

    # --- Vínculos -----------------------------------------------------------------------

    async def memberships(self, user_id: uuid.UUID) -> list[TenantMember]:
        async with global_session(self.db, user_id=user_id) as session:
            rows = await session.execute(
                select(TenantMember).where(TenantMember.user_id == user_id)
            )
            return list(rows.scalars())

    async def choose_tenant(self, token: str, data: PanelSession, tenant_id: uuid.UUID) -> None:
        """Entra na imobiliária: membro com o papel dele; operador como suporte (auditado)."""
        member = next(
            (m for m in await self.memberships(data.user_uuid) if m.tenant_id == tenant_id), None
        )
        if member is None and not data.is_operator:
            raise AuthError("Você não tem acesso a essa imobiliária.")
        async with tenant_session(self.db, tenant_id, user_id=data.user_uuid) as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                raise AuthError("Imobiliária não encontrada.")
            if member is None:
                await append_audit(
                    session, tenant_id=tenant_id, event_type="support_access",
                    payload={"user_id": data.user_id},
                )  # fmt: skip
            data.tenant_name = tenant.name
        data.tenant_id = str(tenant_id)
        data.role = member.role if member else "operador"
        await self.update_session(token, data)

    async def tenant_names(
        self, tenant_ids: list[uuid.UUID], user_id: uuid.UUID
    ) -> dict[uuid.UUID, str]:
        names = {}
        for tenant_id in tenant_ids:
            async with tenant_session(self.db, tenant_id, user_id=user_id) as session:
                tenant = await session.get(Tenant, tenant_id)
                if tenant is not None:
                    names[tenant_id] = tenant.name
        return names
