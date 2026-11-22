"""Processamento de um lote de mensagens de uma conversa (SPEC, seção 3, passos 3-4).

1. Lê as mensagens de entrada ainda não tratadas (transação curta).
2. Portões: imobiliária pausada (RF19), contato com opt-out (RF21), conversa muda
   porque o corretor assumiu (RF11) ou em silêncio anti-abuso (RF20) → não responde.
3. Áudio → texto pela API de transcrição, fora de transação; a referência da mídia
   é apagada logo após o sucesso (o áudio só existiu em memória).
4. Entrega o lote ao `BatchHandler` (extração, decisão e resposta: M5).
5. Marca as mensagens como tratadas.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.transcriber import Transcriber, TranscriptionError
from app.adapters.whatsapp import GatewayError, WhatsAppGateway
from app.core.crypto import Keyring
from app.core.logging import bind_context, get_logger
from app.core.rls import tenant_session
from app.models import Contact, Conversation, Message, Tenant
from app.models.enums import Direction, MessageKind, TenantStatus
from app.workers.channel import ChannelMissingError, channel_for
from app.workers.queue import WorkQueue

log = get_logger(__name__)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class SkipReason(StrEnum):
    NOTHING_PENDING = "nothing_pending"
    TENANT_PAUSED = "tenant_paused"
    TENANT_INACTIVE = "tenant_inactive"  # em implantação ou suspensa: não atende
    OPTED_OUT = "opted_out"
    MUTED = "muted"
    SILENCED = "silenced"


@dataclass(frozen=True)
class BatchMessage:
    id: uuid.UUID
    kind: MessageKind
    text: str | None
    transcription_failed: bool = False


@dataclass(frozen=True)
class InboundBatch:
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    contact_id: uuid.UUID
    messages: tuple[BatchMessage, ...]

    @property
    def combined_text(self) -> str:
        return "\n".join(m.text for m in self.messages if m.text)


class BatchHandler(Protocol):
    async def handle(self, batch: InboundBatch) -> None: ...


@dataclass
class RecordingHandler:
    """Handler de teste e de desenvolvimento: guarda os lotes recebidos."""

    batches: list[InboundBatch] = field(default_factory=list)

    async def handle(self, batch: InboundBatch) -> None:
        self.batches.append(batch)


@dataclass(frozen=True)
class ProcessOutcome:
    handled: bool
    skipped: SkipReason | None = None
    messages: int = 0


@dataclass
class _Pending:
    contact_id: uuid.UUID
    skip: SkipReason | None
    messages: list[Message]


class ConversationProcessor:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        queue: WorkQueue,
        gateway: WhatsAppGateway,
        transcriber: Transcriber,
        keyring: Keyring,
        handler: BatchHandler,
        clock: Clock = utcnow,
    ) -> None:
        self.sessions, self.queue, self.gateway = sessions, queue, gateway
        self.transcriber, self.keyring, self.handler, self.clock = (
            transcriber,
            keyring,
            handler,
            clock,
        )

    async def process(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> ProcessOutcome:
        bind_context(tenant_id=str(tenant_id), conversation_id=str(conversation_id))
        pending = await self._load(tenant_id, conversation_id)
        if not pending.messages:
            return ProcessOutcome(handled=False, skipped=SkipReason.NOTHING_PENDING)
        if pending.skip is None and await self.queue.is_silenced(tenant_id, pending.contact_id):
            pending.skip = SkipReason.SILENCED
        if pending.skip is not None:
            await self._save(tenant_id, pending.messages, [], processed=True)
            log.info("lote_sem_resposta", reason=str(pending.skip), messages=len(pending.messages))
            return ProcessOutcome(
                handled=False, skipped=pending.skip, messages=len(pending.messages)
            )
        batch_messages = await self._transcribe(tenant_id, pending.messages)
        await self._save(tenant_id, pending.messages, batch_messages, processed=False)
        batch = InboundBatch(tenant_id, conversation_id, pending.contact_id, tuple(batch_messages))
        await self.handler.handle(batch)
        # Só depois da resposta: se o handler falhar, o lote é retomado por outro worker.
        await self._save(tenant_id, pending.messages, [], processed=True)
        log.info("lote_processado", messages=len(batch_messages))
        return ProcessOutcome(handled=True, messages=len(batch_messages))

    async def _load(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> _Pending:
        now = self.clock()
        async with tenant_session(self.sessions, tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            conversation = await session.get(Conversation, conversation_id)
            if tenant is None or conversation is None:
                raise LookupError("conversa não encontrada")
            contact = await session.get(Contact, conversation.contact_id)
            messages = list(
                (
                    await session.execute(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.direction == Direction.INBOUND,
                            Message.processed_at.is_(None),
                        )
                        .order_by(Message.created_at)
                    )
                ).scalars()
            )
        skip: SkipReason | None = None
        if tenant.paused_at is not None:
            skip = SkipReason.TENANT_PAUSED
        elif tenant.status not in {TenantStatus.ACTIVE, TenantStatus.SHADOW}:
            skip = SkipReason.TENANT_INACTIVE
        elif contact is None or contact.opted_out_at is not None:
            skip = SkipReason.OPTED_OUT
        elif conversation.muted_until is not None and conversation.muted_until > now:
            skip = SkipReason.MUTED
        return _Pending(conversation.contact_id, skip, messages)

    async def _transcribe(
        self, tenant_id: uuid.UUID, messages: list[Message]
    ) -> list[BatchMessage]:
        result: list[BatchMessage] = []
        for message in messages:
            if message.kind != MessageKind.AUDIO or message.transcript or not message.media_ref:
                text = message.transcript if message.kind == MessageKind.AUDIO else message.body
                result.append(BatchMessage(message.id, MessageKind(message.kind), text))
                continue
            transcript = await self._transcribe_one(tenant_id, message.media_ref)
            result.append(
                BatchMessage(
                    message.id,
                    MessageKind.AUDIO,
                    transcript,
                    transcription_failed=transcript is None,
                )
            )
        return result

    async def _transcribe_one(self, tenant_id: uuid.UUID, media_id: str) -> str | None:
        try:
            async with tenant_session(self.sessions, tenant_id) as session:
                channel = await channel_for(session, tenant_id, self.keyring)
            media = await self.gateway.fetch_media(channel, media_id)
            return await self.transcriber.transcribe(media.content, media.mime_type)
        except (ChannelMissingError, GatewayError, TranscriptionError) as exc:
            log.warning("transcricao_falhou", error_type=type(exc).__name__)
            return None

    async def _save(
        self,
        tenant_id: uuid.UUID,
        messages: list[Message],
        batch: list[BatchMessage],
        *,
        processed: bool,
    ) -> None:
        """Grava transcrições (e apaga a referência da mídia) e, se pedido, marca como tratadas."""
        now = self.clock()
        transcripts = {m.id: m.text for m in batch if m.kind == MessageKind.AUDIO and m.text}
        async with tenant_session(self.sessions, tenant_id) as session:
            for message in messages:
                values: dict[str, object] = {"processed_at": now} if processed else {}
                if message.id in transcripts:
                    values.update(transcript=transcripts[message.id], media_ref=None)
                if not values:
                    continue
                await session.execute(
                    update(Message).where(Message.id == message.id).values(**values)
                )
