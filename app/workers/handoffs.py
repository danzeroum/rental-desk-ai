"""Passagem ao corretor: aviso, assumir, escalonamento, pausa e desfecho (RF10-RF12, RF17, RF19).

Regras de tempo (RF12, esclarecimento de 21/11/2026), em tempo comercial:
- 10 min sem assumir → próximo corretor de plantão no rodízio, e segue a cada 10 min;
- 15 min → alerta ao gestor (WhatsApp) e ao operador (e-mail);
- 60 min → mensagem ao hóspede com previsão de retorno e telefone de contato;
- fora do horário nada dispara: a passagem começa a contar na abertura do plantão.

Envios (WhatsApp, e-mail) acontecem fora de transação; o estado é gravado depois.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.mailer import Mailer, MailError
from app.adapters.whatsapp import Channel, GatewayError, WhatsAppGateway
from app.core.audit import append_audit
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import tenant_session
from app.domain import notices
from app.domain.hours import BusinessHours, InvalidHoursError
from app.domain.rotation import Broker, next_in_cycle, pick_for_new
from app.models import Contact, Conversation, Handoff, Tenant
from app.models.enums import ConversationStatus, HandoffOutcome, HandoffStatus, MemberRole
from app.workers.brokers import (
    brokers_on_duty,
    last_assignments,
    managers_to_copy,
    member_by_phone,
    operator_emails,
)
from app.workers.channel import ChannelMissingError, channel_for
from app.workers.responder import send_reply

ROTATE_AFTER: Final = timedelta(minutes=10)
ALERT_AFTER: Final = timedelta(minutes=15)
GUEST_AFTER: Final = timedelta(minutes=60)
MUTE_FOR: Final = timedelta(hours=24)
ASSUME_WORDS: Final = frozenset({"1", "assumir", "assumo"})

log = get_logger(__name__)
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class HandoffStateError(Exception):
    """Ação incompatível com o estado da passagem; a mensagem vai para o painel."""


def hours_of(tenant: Tenant) -> BusinessHours:
    try:
        return BusinessHours.from_json(tenant.broker_hours)
    except InvalidHoursError:
        log.warning("horario_corretores_invalido")
        return BusinessHours.from_json(None)


@dataclass
class _Snapshot:
    """Dados de uma passagem lidos numa transação curta, para enviar depois."""

    tenant_name: str
    contact_phone: str | None
    guest_phone: str
    reason: str
    summary: str
    conversation_id: uuid.UUID
    channel: Channel | None
    brokers: list[Broker] = field(default_factory=list)
    managers: list[Broker] = field(default_factory=list)


class HandoffService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        gateway: WhatsAppGateway,
        keyring: Keyring,
        mailer: Mailer,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.gateway, self.keyring = sessions, gateway, keyring
        self.mailer, self.clock = mailer, clock

    # --- Nova passagem (RF10) --------------------------------------------------------

    async def dispatch_new(self, tenant_id: uuid.UUID, handoff_id: uuid.UUID) -> None:
        """Dentro do horário: avisa o corretor da vez e copia o gestor. Fora: entra na fila."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant, handoff = await self._load(session, tenant_id, handoff_id)
            if handoff.clock_started_at is not None:
                return  # já despachada (idempotente)
            hours = hours_of(tenant)
            handoff.clock_started_at = hours.next_opening(now)
            open_now = hours.is_open(now)
        if open_now:
            await self._notify_next(tenant_id, handoff_id, first=True)
        else:
            log.info("passagem_na_fila_fora_do_horario", handoff_id=str(handoff_id))

    # --- Escalonamento (RF12) -----------------------------------------------------------

    async def escalate(self, tenant_id: uuid.UUID) -> int:
        """Uma volta do escalonamento para a imobiliária; devolve quantas ações executou."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return 0
            hours = hours_of(tenant)
            if not hours.is_open(now):
                return 0  # fora do horário nada dispara
            pending = list(
                (
                    await session.execute(
                        select(Handoff).where(
                            Handoff.status == HandoffStatus.OPEN,
                            Handoff.clock_started_at.is_not(None),
                            Handoff.clock_started_at <= now,
                        )
                    )
                ).scalars()
            )
        actions = 0
        for handoff in pending:
            actions += await self._escalate_one(tenant_id, handoff, hours, now)
        return actions

    async def _escalate_one(
        self, tenant_id: uuid.UUID, handoff: Handoff, hours: BusinessHours, now: datetime
    ) -> int:
        actions = 0
        started = handoff.clock_started_at or now
        if handoff.assigned_to is None or handoff.notified_at is None:
            actions += await self._notify_next(tenant_id, handoff.id, first=True)
        elif hours.elapsed(handoff.notified_at, now) >= ROTATE_AFTER:
            actions += await self._notify_next(tenant_id, handoff.id, first=False)
        elapsed = hours.elapsed(started, now)
        if elapsed >= ALERT_AFTER and handoff.manager_alerted_at is None:
            actions += await self._alert(tenant_id, handoff.id, elapsed)
        if elapsed >= GUEST_AFTER and handoff.guest_notified_at is None:
            actions += await self._tell_guest_to_wait(tenant_id, handoff.id)
        return actions

    # --- Envios -----------------------------------------------------------------------

    async def _snapshot(
        self, session: AsyncSession, tenant_id: uuid.UUID, handoff: Handoff
    ) -> _Snapshot:
        tenant = await session.get(Tenant, tenant_id)
        conversation = await session.get(Conversation, handoff.conversation_id)
        contact = await session.get(Contact, conversation.contact_id) if conversation else None
        if tenant is None or conversation is None:
            raise LookupError("passagem sem conversa")
        try:
            channel: Channel | None = await channel_for(session, tenant_id, self.keyring)
        except ChannelMissingError:
            channel = None
        return _Snapshot(
            tenant_name=tenant.name,
            contact_phone=tenant.contact_phone,
            guest_phone=(contact.phone_e164 if contact and contact.phone_e164 else "(anonimizado)"),
            reason=notices.reason_label(handoff.reason),
            summary=notices.summary_text(handoff.summary),
            conversation_id=conversation.id,
            channel=channel,
            brokers=await brokers_on_duty(session),
            managers=await managers_to_copy(session),
        )

    async def _send_template(
        self, snap: _Snapshot, to: Broker, template: notices.Template, params: list[str]
    ) -> bool:
        if snap.channel is None:
            log.error("passagem_sem_numero_conectado")
            return False
        try:
            await self.gateway.send_template(
                snap.channel, to.phone_e164.removeprefix("+"), template.name, params
            )
        except GatewayError as exc:
            log.warning("aviso_passagem_falhou", template=template.name, status=exc.status)
            return False
        return True

    async def _notify_next(
        self, tenant_id: uuid.UUID, handoff_id: uuid.UUID, *, first: bool
    ) -> int:
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            _, handoff = await self._load(session, tenant_id, handoff_id)
            if handoff.status != HandoffStatus.OPEN:
                return 0
            snap = await self._snapshot(session, tenant_id, handoff)
            if first and handoff.assigned_to is None:
                broker = pick_for_new(snap.brokers, await last_assignments(session))
            else:
                broker = next_in_cycle(snap.brokers, handoff.assigned_to)
        if broker is None:
            log.warning("passagem_sem_corretor_de_plantao", handoff_id=str(handoff_id))
            async with tenant_session(self.sessions, tenant_id) as session:
                _, handoff = await self._load(session, tenant_id, handoff_id)
                handoff.notified_at = now  # espera o próximo ciclo; o alerta dos 15 min cobre
            return 0
        sent = await self._send_template(
            snap, broker, notices.HANDOFF_TO_BROKER,
            [snap.tenant_name, snap.reason, snap.guest_phone, snap.summary],
        )  # fmt: skip
        copies = 0
        if sent and first:
            for manager in snap.managers:
                copies += await self._send_template(
                    snap, manager, notices.HANDOFF_COPY_TO_MANAGER,
                    [snap.tenant_name, snap.reason, snap.guest_phone, broker.name, snap.summary],
                )  # fmt: skip
        async with tenant_session(self.sessions, tenant_id) as session:
            _, handoff = await self._load(session, tenant_id, handoff_id)
            handoff.notified_at = now
            if sent:
                handoff.assigned_to, handoff.assigned_at = broker.user_id, now
                handoff.notify_count += 1
            await append_audit(
                session, tenant_id=tenant_id, conversation_id=handoff.conversation_id,
                event_type="handoff_notified",
                payload={"handoff_id": str(handoff_id), "broker_id": str(broker.user_id),
                         "sent": sent, "rotation": not first, "manager_copies": copies},
            )  # fmt: skip
        return 1

    async def _alert(self, tenant_id: uuid.UUID, handoff_id: uuid.UUID, elapsed: timedelta) -> int:
        minutes = str(int(elapsed.total_seconds() // 60))
        async with tenant_session(self.sessions, tenant_id) as session:
            _, handoff = await self._load(session, tenant_id, handoff_id)
            snap = await self._snapshot(session, tenant_id, handoff)
            emails = await operator_emails(session)
        sent = 0
        for manager in snap.managers:
            sent += await self._send_template(
                snap, manager, notices.HANDOFF_ALERT,
                [snap.tenant_name, snap.reason, snap.guest_phone, minutes],
            )  # fmt: skip
        for email in emails:
            try:
                await self.mailer.send(
                    email,
                    f"[{snap.tenant_name}] passagem sem corretor há {minutes} min",
                    f"A passagem ({snap.reason}) está sem corretor há {minutes} minutos "
                    f"comerciais. Pedido: {snap.summary}.",
                )
                sent += 1
            except MailError:
                log.warning("alerta_operador_falhou")
        async with tenant_session(self.sessions, tenant_id) as session:
            _, handoff = await self._load(session, tenant_id, handoff_id)
            handoff.manager_alerted_at = self.clock()
            await append_audit(
                session, tenant_id=tenant_id, conversation_id=handoff.conversation_id,
                event_type="handoff_alerted",
                payload={"handoff_id": str(handoff_id), "minutes": minutes, "sent": sent},
            )  # fmt: skip
        return 1

    async def _tell_guest_to_wait(self, tenant_id: uuid.UUID, handoff_id: uuid.UUID) -> int:
        async with tenant_session(self.sessions, tenant_id) as session:
            _, handoff = await self._load(session, tenant_id, handoff_id)
            snap = await self._snapshot(session, tenant_id, handoff)
            handoff.guest_notified_at = self.clock()  # marca antes: não repetir se o envio falhar
        try:
            await send_reply(
                self.sessions, self.gateway, self.keyring,
                tenant_id=tenant_id, conversation_id=snap.conversation_id,
                text=notices.guest_waiting_text(snap.contact_phone),
                event_type="handoff_guest_waiting", audit_payload={"handoff_id": str(handoff_id)},
                snapshot_hash=None, now=self.clock(),
            )  # fmt: skip
        except (GatewayError, ChannelMissingError) as exc:
            log.warning("aviso_hospede_falhou", error_type=type(exc).__name__)
        return 1

    # --- Assumir (RF11) ----------------------------------------------------------------

    async def assume(
        self, tenant_id: uuid.UUID, handoff_id: uuid.UUID, user_id: uuid.UUID, *, via: str
    ) -> Handoff:
        """Painel ou "responda 1": conversa muda por 24 h e passa a humano."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            handoff = await session.get(Handoff, handoff_id, with_for_update=True)
            if handoff is None:
                raise HandoffStateError("passagem não encontrada")
            if handoff.status != HandoffStatus.OPEN:
                raise HandoffStateError(notices.BROKER_ALREADY_TAKEN)
            handoff.status = HandoffStatus.ASSUMED
            handoff.assumed_at, handoff.assumed_by, handoff.assumed_via = now, user_id, via
            await session.execute(
                update(Conversation)
                .where(Conversation.id == handoff.conversation_id)
                .values(muted_until=now + MUTE_FOR, status=ConversationStatus.HUMAN)
            )
            await append_audit(
                session, tenant_id=tenant_id, conversation_id=handoff.conversation_id,
                event_type="handoff_assumed",
                payload={"handoff_id": str(handoff_id), "user_id": str(user_id), "via": via},
            )  # fmt: skip
        log.info("passagem_assumida", via=via)
        return handoff

    async def handle_member_message(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, phone_e164: str, text: str
    ) -> bool:
        """Mensagem de corretor/gestor ao número da imobiliária. `True` se foi tratada."""
        async with tenant_session(self.sessions, tenant_id) as session:
            member = await member_by_phone(session, phone_e164)
            if member is None:
                return False
            query = select(Handoff).where(Handoff.status == HandoffStatus.OPEN)
            if member.role == MemberRole.CORRETOR:
                query = query.where(Handoff.assigned_to == member.user_id)
            pending = await session.scalar(
                query.order_by(Handoff.notified_at.desc().nulls_last(), Handoff.created_at.desc())
            )
            user_id, pending_id = member.user_id, pending.id if pending else None
        wants_to_assume = text.strip().lower() in ASSUME_WORDS
        reply = notices.BROKER_NOTHING_PENDING if wants_to_assume else notices.MEMBER_HELP
        if wants_to_assume and pending_id is not None:
            try:
                handoff = await self.assume(tenant_id, pending_id, user_id, via="whatsapp")
                async with tenant_session(self.sessions, tenant_id) as session:
                    snap = await self._snapshot(session, tenant_id, handoff)
                reply = notices.BROKER_ASSUMED.format(guest=snap.guest_phone)
            except HandoffStateError as exc:
                reply = str(exc)
        await send_reply(
            self.sessions, self.gateway, self.keyring,
            tenant_id=tenant_id, conversation_id=conversation_id, text=reply,
            event_type="broker_reply", audit_payload={"user_id": str(user_id)},
            snapshot_hash=None, now=self.clock(),
        )  # fmt: skip
        return True

    # --- Desfecho (RF17) e pausa (RF19) ----------------------------------------------

    async def record_outcome(
        self,
        tenant_id: uuid.UUID,
        handoff_id: uuid.UUID,
        outcome: HandoffOutcome,
        user_id: uuid.UUID,
    ) -> Handoff:
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            handoff = await session.get(Handoff, handoff_id, with_for_update=True)
            if handoff is None:
                raise HandoffStateError("passagem não encontrada")
            handoff.outcome, handoff.outcome_at, handoff.outcome_by = outcome, now, user_id
            if outcome in {HandoffOutcome.WON, HandoffOutcome.LOST}:
                handoff.status = HandoffStatus.CLOSED
            await append_audit(
                session, tenant_id=tenant_id, conversation_id=handoff.conversation_id,
                event_type="handoff_outcome",
                payload={
                    "handoff_id": str(handoff_id),
                    "outcome": str(outcome),
                    "user_id": str(user_id),
                },
            )  # fmt: skip
        return handoff

    async def set_paused(self, tenant_id: uuid.UUID, *, paused: bool, user_id: uuid.UUID) -> None:
        """RF19: efeito imediato (o processador confere `paused_at` a cada lote)."""
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id, user_id=user_id) as session:
            await session.execute(
                update(Tenant)
                .where(Tenant.id == tenant_id)
                .values(paused_at=now if paused else None)
            )
            await append_audit(
                session, tenant_id=tenant_id,
                event_type="assistant_paused" if paused else "assistant_resumed",
                payload={"user_id": str(user_id), "at": now.isoformat()},
            )  # fmt: skip
        log.info("assistente_pausado" if paused else "assistente_retomado")

    @staticmethod
    async def _load(
        session: AsyncSession, tenant_id: uuid.UUID, handoff_id: uuid.UUID
    ) -> tuple[Tenant, Handoff]:
        tenant = await session.get(Tenant, tenant_id)
        handoff = await session.get(Handoff, handoff_id, with_for_update=True)
        if tenant is None or handoff is None:
            raise LookupError("passagem não encontrada")
        return tenant, handoff
