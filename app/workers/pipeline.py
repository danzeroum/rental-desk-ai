"""Pipeline de resposta ligado ao banco (SPEC, seção 3, passos 5 a 11).

Implementa o `BatchHandler` do processador: carrega o contexto da conversa,
chama o motor, envia a resposta (ou só registra, no modo sombra) e grava estado,
intenção, resultado da busca, passagem ao corretor e trilha de auditoria.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.whatsapp import WhatsAppGateway
from app.core.audit import append_audit
from app.core.config import TIMEZONE
from app.core.crypto import Keyring
from app.core.logging import get_logger
from app.core.rls import tenant_session
from app.domain.catalog import BlockData, PropertyData, RateData
from app.domain.extraction import SCHEMA_VERSION
from app.domain.prompts import HistoryItem
from app.domain.search import is_data_stale
from app.domain.state import load_state
from app.models import (
    Block,
    Conversation,
    DataSnapshot,
    Handoff,
    Intent,
    Message,
    PrivacyNotice,
    Property,
    Rate,
    SearchResult,
    Tenant,
)
from app.models.enums import Direction, TenantStatus
from app.workers.engine import Catalog, ConversationEngine, TurnContext, TurnResult
from app.workers.handoffs import HandoffService, hours_of
from app.workers.processor import InboundBatch
from app.workers.responder import send_reply

HISTORY_LIMIT: Final = 10
log = get_logger(__name__)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


_PROPERTY_FIELDS: Final = tuple(PropertyData.__dataclass_fields__)
_RATE_FIELDS: Final = tuple(RateData.__dataclass_fields__)


async def load_catalog(session: AsyncSession, tenant: Tenant) -> Catalog | None:
    """Versão aprovada em uso + bloqueios atuais; `None` sem dados aprovados."""
    if tenant.active_snapshot_id is None:
        return None
    snapshot = await session.get(DataSnapshot, tenant.active_snapshot_id)
    if snapshot is None:
        return None
    properties = (
        await session.execute(select(Property).where(Property.snapshot_id == snapshot.id))
    ).scalars()
    rates = (await session.execute(select(Rate).where(Rate.snapshot_id == snapshot.id))).scalars()
    blocks = (await session.execute(select(Block))).scalars()
    return Catalog(
        snapshot_hash=snapshot.content_hash,
        properties=[
            PropertyData(**{f: getattr(p, f) for f in _PROPERTY_FIELDS}) for p in properties
        ],
        rates=[RateData(**{f: getattr(r, f) for f in _RATE_FIELDS}) for r in rates],
        blocks=[BlockData(b.property_code, b.start_date, b.end_date) for b in blocks],
    )


@dataclass(frozen=True)
class _Loaded:
    context: TurnContext
    tenant_status: str
    snapshot_id: uuid.UUID | None


class ResponsePipeline:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        engine: ConversationEngine,
        gateway: WhatsAppGateway,
        keyring: Keyring,
        public_base_url: str,
        handoffs: HandoffService | None = None,
        clock: Clock = _utcnow,
    ) -> None:
        self.sessions, self.engine, self.gateway, self.keyring = sessions, engine, gateway, keyring
        self.public_base_url = public_base_url.rstrip("/")
        self.handoffs = handoffs
        self.clock = clock

    async def handle(self, batch: InboundBatch) -> None:
        loaded = await self._load(batch)
        result = await self.engine.run(loaded.context)
        payload = self._audit_payload(loaded, result)
        catalog = loaded.context.catalog
        if loaded.tenant_status == TenantStatus.SHADOW:
            await self._record_shadow(batch, result, payload)
        else:
            await send_reply(
                self.sessions, self.gateway, self.keyring,
                tenant_id=batch.tenant_id, conversation_id=batch.conversation_id,
                text=result.text, event_type="response_sent", audit_payload=payload,
                snapshot_hash=catalog.snapshot_hash if catalog else None,
                now=self.clock(),
            )  # fmt: skip
        shadow = loaded.tenant_status == TenantStatus.SHADOW
        # Em sombra o hóspede é atendido pela equipe: a passagem que o assistente faria fica
        # só no registro (`payload.handoff`), sem passagem real nem aviso ao corretor.
        handoff_id = await self._persist(batch, loaded, result, create_handoff=not shadow)
        if handoff_id is not None and self.handoffs is not None:
            await self.handoffs.dispatch_new(batch.tenant_id, handoff_id)

    async def _load(self, batch: InboundBatch) -> _Loaded:
        now = self.clock()
        async with tenant_session(self.sessions, batch.tenant_id) as session:
            tenant = await session.get(Tenant, batch.tenant_id)
            conversation = await session.get(Conversation, batch.conversation_id)
            if tenant is None or conversation is None:
                raise LookupError("conversa não encontrada")
            batch_ids = {m.id for m in batch.messages}
            recent = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_LIMIT + len(batch_ids))
                )
            ).scalars()
            history = [
                HistoryItem(m.direction == Direction.INBOUND, m.body or m.transcript or "")
                for m in reversed(list(recent))
                if m.id not in batch_ids and (m.body or m.transcript)
            ][-HISTORY_LIMIT:]
            notice_version = await session.scalar(
                select(PrivacyNotice.version).order_by(PrivacyNotice.version.desc()).limit(1)
            )
            catalog = await load_catalog(session, tenant)
            hours = hours_of(tenant)
            context = TurnContext(
                today=now.astimezone(TIMEZONE).date(),
                tenant_name=tenant.name,
                assistant_name=tenant.assistant_name,
                tone=tenant.tone,
                privacy_url=self._privacy_url(tenant.slug, notice_version),
                first_response=conversation.first_response_at is None,
                state=load_state(conversation.state_json),
                history=history,
                new_text=batch.combined_text or None,
                catalog=catalog,
                data_fresh=not is_data_stale(tenant.availability_updated_at, now),
                audio_not_understood=any(m.transcription_failed for m in batch.messages),
                brokers_back=None if hours.is_open(now) else hours.describe_next_opening(now),
            )
            return _Loaded(context, tenant.status, tenant.active_snapshot_id)

    def _privacy_url(self, slug: str, version: int | None) -> str:
        suffix = f"/v{version}" if version else ""
        return f"{self.public_base_url}/privacidade/{slug}{suffix}"

    @staticmethod
    def _audit_payload(loaded: _Loaded, result: TurnResult) -> dict[str, Any]:
        """SPEC, seção 7, item 9: hash do snapshot, extração, busca, texto final, validador."""
        search: dict[str, Any] | None = None
        if result.search is not None:
            search = {
                "query": result.search_query,
                "data_stale": result.search.data_stale,
                "over_budget": result.search.over_budget,
                "results": [
                    {
                        "code": o.property.code,
                        "total_cents": o.quote.total_cents,
                        "flags": sorted(str(f) for f in o.quote.flags),
                    }
                    for o in result.search.offers
                ],
            }
        decision = result.decision
        return {
            "extraction": result.extraction,
            "decision": {
                "action": str(decision.action) if decision else None,
                "reason": str(decision.reason) if decision and decision.reason else None,
                "missing": list(decision.missing) if decision else [],
            },
            "search": search,
            "validator": result.validator,
            "handoff": result.handoff.reason if result.handoff else None,
            "llm_unavailable": result.llm_unavailable,
            "llm_calls": result.llm_calls,
            # Fallback de recusa acionado: qual modelo recusou e qual respondeu (pedido do time).
            "llm_refusal_fallback": [
                {"from": c["fallback_from"], "to": c["model"], "purpose": c["purpose"]}
                for c in result.refusal_fallbacks
            ]
            or None,
            "first_response": loaded.context.first_response,
        }

    async def _record_shadow(
        self, batch: InboundBatch, result: TurnResult, payload: dict[str, Any]
    ) -> None:
        """Modo sombra (SPEC, seção 11): grava a resposta que daria, sem enviar."""
        async with tenant_session(self.sessions, batch.tenant_id) as session:
            await append_audit(
                session,
                tenant_id=batch.tenant_id,
                conversation_id=batch.conversation_id,
                event_type="shadow_response",
                snapshot_hash=None,
                payload={**payload, "text": result.text},
            )
        log.info("resposta_sombra_registrada")

    async def _persist(
        self, batch: InboundBatch, loaded: _Loaded, result: TurnResult, *, create_handoff: bool
    ) -> uuid.UUID | None:
        """Grava estado, intenção, busca e passagem; devolve o id da passagem criada."""
        async with tenant_session(self.sessions, batch.tenant_id) as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == batch.conversation_id)
                .values(state_json=result.state.to_json())
            )
            if result.extraction is not None:
                session.add(
                    Intent(
                        tenant_id=batch.tenant_id,
                        conversation_id=batch.conversation_id,
                        schema_version=SCHEMA_VERSION,
                        payload=result.extraction,
                    )
                )
            if result.search is not None and loaded.snapshot_id is not None:
                session.add(
                    SearchResult(
                        tenant_id=batch.tenant_id,
                        conversation_id=batch.conversation_id,
                        snapshot_id=loaded.snapshot_id,
                        query=result.search_query or {},
                        results=[
                            {"code": o.property.code, "total_cents": o.quote.total_cents}
                            for o in result.search.offers
                        ],
                    )
                )
            if result.handoff is None or not create_handoff:
                return None
            handoff = Handoff(
                tenant_id=batch.tenant_id,
                conversation_id=batch.conversation_id,
                reason=result.handoff.reason,
                summary=result.handoff.summary,
            )
            session.add(handoff)
            await session.flush()
            return handoff.id
