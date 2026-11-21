"""Criação de dados de teste: um tenant com pelo menos uma linha em cada tabela."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.rls import global_session, tenant_session
from app.models import (
    AppUser,
    AuditLog,
    Block,
    Consent,
    Contact,
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
    TenantMember,
    TenantRoute,
    WeeklyReport,
    WhatsAppAccount,
)
from app.models.enums import (
    BlockSource,
    ConsentPurpose,
    Direction,
    MemberRole,
    MessageKind,
    SnapshotSource,
    SnapshotStatus,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def create_tenant(
    ops: async_sessionmaker[AsyncSession], name: str = "Imobiliária"
) -> uuid.UUID:
    """Cria a imobiliária com o papel do operador (o app não insere tenants)."""
    tenant_id = uuid.uuid4()
    async with ops() as session, session.begin():
        session.add(Tenant(id=tenant_id, name=name, slug=f"t-{tenant_id.hex[:12]}"))
    return tenant_id


async def create_user(
    app: async_sessionmaker[AsyncSession], *, operator: bool = False
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with global_session(app) as session:
        session.add(
            AppUser(
                id=user_id,
                email=f"u-{user_id.hex[:12]}@exemplo.com",
                display_name="Pessoa de Teste",
                is_operator=operator,
            )
        )
    return user_id


async def seed_tenant_data(
    app: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """Uma linha (ou mais) em cada tabela de negócio, usando o papel da aplicação."""
    ids: dict[str, Any] = {}
    now = datetime.now(UTC)
    async with tenant_session(app, tenant_id, user_id=user_id) as s:
        snapshot = DataSnapshot(
            tenant_id=tenant_id,
            version=1,
            source=SnapshotSource.UPLOAD,
            status=SnapshotStatus.APPROVED,
            content_hash=_sha(str(tenant_id)),
            approved_at=now,
            approved_by=user_id,
        )
        notice = PrivacyNotice(
            tenant_id=tenant_id, version=1, content="Aviso", content_hash=_sha("Aviso")
        )
        contact = Contact(tenant_id=tenant_id, phone_e164=f"+55119{uuid.uuid4().int % 10**8:08d}")
        s.add_all([snapshot, notice, contact])
        await s.flush()
        conversation = Conversation(
            tenant_id=tenant_id, contact_id=contact.id, privacy_notice_id=notice.id
        )
        s.add(conversation)
        await s.flush()
        s.add_all(
            [
                TenantMember(tenant_id=tenant_id, user_id=user_id, role=MemberRole.GESTOR),
                TenantRoute(channel="whatsapp", external_id=uuid.uuid4().hex, tenant_id=tenant_id),
                WhatsAppAccount(
                    tenant_id=tenant_id, phone_number_id=uuid.uuid4().hex, display_phone="+5511"
                ),
                Property(
                    tenant_id=tenant_id,
                    snapshot_id=snapshot.id,
                    code="CASA01",
                    title="Casa de teste",
                    max_guests=10,
                    has_pool=True,
                ),
                Rate(
                    tenant_id=tenant_id,
                    snapshot_id=snapshot.id,
                    property_code="CASA01",
                    start_date=date(2026, 12, 1),
                    end_date=date(2027, 3, 1),
                    daily_cents=150_000,
                ),
                Block(
                    tenant_id=tenant_id,
                    property_code="CASA01",
                    start_date=date(2026, 12, 28),
                    end_date=date(2027, 1, 2),
                    source=BlockSource.PANEL,
                ),
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    direction=Direction.INBOUND,
                    kind=MessageKind.TEXT,
                    external_id=f"wamid.{uuid.uuid4().hex}",
                    body="Casa para 12 no Réveillon",
                ),
                Intent(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    schema_version=1,
                    payload={"intent": "search"},
                ),
                SearchResult(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    snapshot_id=snapshot.id,
                    query={"guests": 12},
                    results=[],
                ),
                Handoff(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    reason="choose",
                    summary={"pedido": "teste"},
                ),
                Consent(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    phone_e164="+5511999999999",
                    purpose=ConsentPurpose.WEEKLY_REPORT,
                    granted_at=now,
                    evidence="aceite no painel",
                ),
                WeeklyReport(
                    tenant_id=tenant_id,
                    period_start=date(2026, 11, 16),
                    period_end=date(2026, 11, 23),
                    metrics={"contatos": 1},
                ),
                AuditLog(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    event_type="response_sent",
                    snapshot_hash=snapshot.content_hash,
                    payload={"texto": "ok"},
                ),
            ]
        )
        await s.flush()
        ids.update(snapshot_id=snapshot.id, conversation_id=conversation.id, contact_id=contact.id)
    return ids
