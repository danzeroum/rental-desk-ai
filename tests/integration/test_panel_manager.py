"""Painel do gestor: planilha, datas, passagens, pausa, configurações e avisos (RF13-RF19, RF24)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.mailer import FakeMailer
from app.core.config import Settings
from app.core.rls import global_session, tenant_session
from app.importer.sample import build_sample
from app.importer.workbook import build_workbook
from app.models import (
    AppUser,
    AuditLog,
    Block,
    Consent,
    Contact,
    Conversation,
    DataSnapshot,
    Handoff,
    PrivacyNotice,
    Tenant,
    TenantMember,
)
from app.models.enums import BlockSource, HandoffStatus, MemberRole, SnapshotStatus
from tests.factories import create_tenant
from tests.integration.panel_client import PanelClient, panel_client, user_with_password

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class Team:
    tenant_id: uuid.UUID
    gestor_id: uuid.UUID
    gestor_email: str
    corretor_id: uuid.UUID
    corretor_email: str


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


async def _member(app: Sessions, tenant_id: uuid.UUID, role: MemberRole) -> tuple[uuid.UUID, str]:
    user_id, email, hashed = user_with_password(role.value)
    async with global_session(app) as session:
        session.add(
            AppUser(id=user_id, email=email, display_name=role.value.title(), password_hash=hashed)
        )
    async with tenant_session(app, tenant_id) as session:
        session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role=role))
    return user_id, email


@pytest.fixture
async def team(app_sessions: Sessions, ops_sessions: Sessions) -> Team:
    tenant_id = await create_tenant(ops_sessions, "Imobiliária Sol")
    gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    corretor = await _member(app_sessions, tenant_id, MemberRole.CORRETOR)
    return Team(tenant_id, *gestor, *corretor)


def _upload(panel: PanelClient, content: bytes, name: str = "casas.xlsx") -> Response:
    return panel.http.post(
        "/painel/dados", data={"csrf": panel.csrf()}, files={"arquivo": (name, content, XLSX)},
        follow_redirects=False,
    )  # fmt: skip


def _approve_sample(panel: PanelClient) -> str:
    response = _upload(panel, build_workbook(build_sample(2026)))
    location = str(response.headers["location"])
    assert panel.post(f"{location}/aprovar").status_code == 303
    return location


async def _count(app: Sessions, tenant_id: uuid.UUID, model: Any, *where: Any) -> int:
    async with tenant_session(app, tenant_id) as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# --- Planilha ---------------------------------------------------------------------------


async def test_upload_preview_and_approve(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.gestor_email)
    home = panel.get("/painel")
    assert "Nenhuma versão aprovada" in home.text and "Vencidas" in home.text
    response = _upload(panel, build_workbook(build_sample(2026)))
    assert response.status_code == 303
    preview = panel.get(response.headers["location"])
    assert (
        "10 casas" in preview.text
        and "Aprovar e pôr no ar" in preview.text
        and "R$" in preview.text
    )
    assert "Aguardando aprovação" in panel.get("/painel/dados").text
    assert (
        await _count(
            app_sessions, team.tenant_id, DataSnapshot, DataSnapshot.status == SnapshotStatus.DRAFT
        )
        == 1
    )
    approved = panel.post(f"{response.headers['location']}/aprovar")
    assert approved.status_code == 303
    home = panel.get("/painel")
    assert "Versão 1 no ar" in home.text and "Em dia" in home.text
    again = panel.post(f"{response.headers['location']}/aprovar")
    assert again.status_code == 409 and "não está aguardando aprovação" in again.text


async def test_upload_with_errors_saves_nothing(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.gestor_email)
    response = _upload(panel, b"isto nao e uma planilha")
    assert response.status_code == 400
    assert "Nada foi gravado" in response.text
    assert await _count(app_sessions, team.tenant_id, DataSnapshot) == 0
    too_big = _upload(panel, b"x" * (5 * 1024 * 1024 + 1))
    assert too_big.status_code == 413


async def test_template_download(panel: PanelClient, team: Team) -> None:
    panel.login(team.corretor_email)
    response = panel.get("/painel/dados/modelo")
    assert response.status_code == 200 and response.content.startswith(b"PK")


async def test_corretor_cannot_manage(panel: PanelClient, team: Team) -> None:
    panel.login(team.corretor_email)
    assert panel.get("/painel/dados").status_code == 403
    assert panel.get("/painel/configuracoes").status_code == 403
    assert panel.post("/painel/pausa", {"action": "pausar"}).status_code == 403
    assert "Planilha" not in panel.get("/painel").text.split("</nav>")[0]


# --- Datas -------------------------------------------------------------------------------


async def test_blocks_add_remove_and_confirm(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.gestor_email)
    _approve_sample(panel)
    page = panel.get("/painel/bloqueios")
    code = re.findall(r"<option>(\w+)</option>", page.text)[0]
    bad = panel.post(
        "/painel/bloqueios", {"codigo": code, "entrada": "2026-12-28", "saida": "2026-12-28"}
    )
    assert bad.status_code == 200 and "A saída precisa ser depois da entrada" in bad.text
    unknown = panel.post(
        "/painel/bloqueios", {"codigo": "XX99", "entrada": "2026-12-28", "saida": "2027-01-02"}
    )
    assert "não está na planilha aprovada" in unknown.text
    added = panel.post(
        "/painel/bloqueios",
        {"codigo": code.lower(), "entrada": "2026-12-28", "saida": "2027-01-02"},
    )
    assert added.status_code == 303
    async with tenant_session(app_sessions, team.tenant_id) as session:
        block = await session.scalar(select(Block).where(Block.source == BlockSource.PANEL))
        sheet_block = await session.scalar(
            select(Block).where(Block.source == BlockSource.SPREADSHEET)
        )
    assert block is not None and block.property_code == code and str(block.end_date) == "2027-01-02"
    assert "28/12/2026" in panel.get("/painel/bloqueios").text
    if sheet_block is not None:
        assert panel.post(f"/painel/bloqueios/{sheet_block.id}/remover").status_code == 409
    assert panel.post(f"/painel/bloqueios/{block.id}/remover").status_code == 303
    assert await _count(app_sessions, team.tenant_id, Block, Block.source == BlockSource.PANEL) == 0
    panel.clock.advance(hours=25)
    assert "Vencidas" in panel.get("/painel").text
    assert panel.post("/painel/bloqueios/confirmar").status_code == 303
    assert "Em dia" in panel.get("/painel").text
    events = await _count(
        app_sessions, team.tenant_id, AuditLog,
        AuditLog.event_type.in_(["block_added", "block_removed", "availability_confirmed"]),
    )  # fmt: skip
    assert events == 3
    async with tenant_session(app_sessions, team.tenant_id) as session:
        audited = await session.scalar(
            select(AuditLog.payload).where(AuditLog.event_type == "block_added")
        )
    assert audited is not None and audited["via"] == "panel" and "at" in audited
    assert audited["user_id"] == str(team.gestor_id)


# --- Passagens e pausa -------------------------------------------------------------------


async def _handoff(app: Sessions, tenant_id: uuid.UUID) -> uuid.UUID:
    async with tenant_session(app, tenant_id) as session:
        notice = PrivacyNotice(
            tenant_id=tenant_id, version=1, content="Aviso", content_hash="0" * 64
        )
        contact = Contact(tenant_id=tenant_id, phone_e164="+5511977776666")
        session.add_all([notice, contact])
        await session.flush()
        conversation = Conversation(
            tenant_id=tenant_id, contact_id=contact.id, privacy_notice_id=notice.id
        )
        session.add(conversation)
        await session.flush()
        handoff = Handoff(
            tenant_id=tenant_id, conversation_id=conversation.id, reason="choose",
            summary={"pedido": {"guests": 8}, "casa_escolhida": "CASA03"},
        )  # fmt: skip
        session.add(handoff)
        await session.flush()
        return handoff.id


async def test_handoff_assume_and_outcome(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    handoff_id = await _handoff(app_sessions, team.tenant_id)
    panel.login(team.corretor_email)
    page = panel.get("/painel/passagens")
    assert (
        "+5511977776666" in page.text
        and "8 pessoas" in page.text
        and "escolheu CASA03" in page.text
    )
    assert panel.post(f"/painel/passagens/{handoff_id}/assumir").status_code == 303
    again = panel.post(f"/painel/passagens/{handoff_id}/assumir")
    assert again.status_code == 409 and "assumida" in again.text
    assert (
        panel.post(f"/painel/passagens/{handoff_id}/desfecho", {"outcome": "won"}).status_code
        == 403
    )
    panel.post("/sair")
    panel.login(team.gestor_email)
    assert "Corretor" in panel.get("/painel/passagens?show=sem-desfecho").text
    evil = panel.post(
        f"/painel/passagens/{handoff_id}/desfecho",
        {"outcome": "won", "next": "https://evil.example"},
    )
    assert evil.headers["location"].startswith("/painel/")
    async with tenant_session(app_sessions, team.tenant_id) as session:
        handoff = await session.get(Handoff, handoff_id)
    assert (
        handoff is not None
        and handoff.status == HandoffStatus.CLOSED
        and handoff.assumed_via == "panel"
    )
    assert "Fechou" in panel.get("/painel/passagens?show=todas").text
    assert (
        panel.post(f"/painel/passagens/{handoff_id}/desfecho", {"outcome": "talvez"}).status_code
        == 400
    )


async def test_pause_and_resume(panel: PanelClient, team: Team, app_sessions: Sessions) -> None:
    panel.login(team.gestor_email)
    panel.post("/painel/pausa", {"action": "pausar"})
    assert "Pausado" in panel.get("/painel").text
    async with tenant_session(app_sessions, team.tenant_id) as session:
        tenant = await session.get(Tenant, team.tenant_id)
    assert tenant is not None and tenant.paused_at is not None
    panel.post("/painel/pausa", {"action": "retomar"})
    assert "Atendendo" in panel.get("/painel").text


# --- Configurações e equipe ---------------------------------------------------------------


def _settings_form(**overrides: str) -> dict[str, str]:
    form = {"assistant_name": "Lia", "tone": "casual", "contact_phone": "(48) 3333-4444"}
    for day in range(5):
        form |= {f"d{day}_inicio": "08:00", f"d{day}_fim": "19:00"}
    form |= {"d5_inicio": "09:00", "d5_fim": "13:00"}
    return form | overrides


async def test_settings_saved_and_validated(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.gestor_email)
    assert panel.post("/painel/configuracoes", _settings_form()).status_code == 303
    async with tenant_session(app_sessions, team.tenant_id) as session:
        tenant = await session.get(Tenant, team.tenant_id)
    assert tenant is not None
    assert (tenant.assistant_name, tenant.tone, tenant.contact_phone) == (
        "Lia",
        "casual",
        "+554833334444",
    )
    assert tenant.broker_hours["5"] == [["09:00", "13:00"]] and "6" not in tenant.broker_hours
    page = panel.get("/painel/configuracoes").text
    assert 'value="19:00"' in page and "Descontraído" in page
    for bad, message in [
        ({"d0_fim": ""}, "preencha início e fim"),
        ({"d0_inicio": "20:00"}, "depois do início"),
        ({"contact_phone": "123"}, "DDD"),
        ({"tone": "pirata"}, "dois tons"),
        ({"assistant_name": ""}, "nome do assistente"),
    ]:
        response = panel.post("/painel/configuracoes", _settings_form(**bad))
        assert response.status_code == 400 and message in response.text, message


async def test_invite_and_remove_member(
    panel: PanelClient, team: Team, app_sessions: Sessions, settings: Settings
) -> None:
    panel.login(team.gestor_email)
    mailer: FakeMailer = panel.app.state.mailer
    response = panel.post(
        "/painel/membros", {"name": "Caio", "email": "Caio@Exemplo.com", "role": "corretor"}
    )
    assert response.status_code == 303
    assert mailer.sent[-1].to == "caio@exemplo.com" and "/convite/" in mailer.sent[-1].body
    assert "Imobiliária Sol" in mailer.sent[-1].body
    duplicate = panel.post(
        "/painel/membros", {"name": "Caio", "email": "caio@exemplo.com", "role": "gestor"}
    )
    assert duplicate.status_code == 400 and "já faz parte" in duplicate.text
    existing = panel.post(
        "/painel/membros", {"name": "X", "email": team.gestor_email, "role": "gestor"}
    )
    assert "já faz parte" in existing.text
    with panel_client(settings, flush=False) as corretor:
        corretor.app.state.auth.clock = panel.clock
        corretor.clock = panel.clock
        corretor.login(team.corretor_email)
        assert corretor.get("/painel").status_code == 200
        assert panel.post(f"/painel/membros/{team.corretor_id}/remover").status_code == 303
        assert corretor.get("/painel").headers["location"] == "/login"
    assert panel.post(f"/painel/membros/{team.gestor_id}/remover").status_code == 400


# --- Meus avisos (consentimento) -----------------------------------------------------------


async def _consents(app: Sessions, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Consent]:
    async with tenant_session(app, tenant_id) as session:
        rows = await session.execute(
            select(Consent).where(Consent.user_id == user_id).order_by(Consent.granted_at)
        )
        return list(rows.scalars())


async def test_alert_consent_given_changed_and_revoked(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.corretor_email)
    page = panel.get("/painel/avisos").text
    assert "handoff_alert" in page and "daily_reminder" not in page  # corretor não recebe lembrete
    missing = panel.post("/painel/avisos", {"phone": "(11) 98888-7777", "handoff_alert": "1"})
    assert missing.status_code == 400 and "concordância" in missing.text
    panel.post(
        "/painel/avisos", {"phone": "(11) 98888-7777", "handoff_alert": "1", "aceite": "sim"}
    )
    [consent] = await _consents(app_sessions, team.tenant_id, team.corretor_id)
    assert consent.phone_e164 == "+5511988887777" and consent.revoked_at is None
    assert "próprio usuário" in consent.evidence
    panel.post("/painel/avisos", {"phone": "11 97777-6666", "handoff_alert": "1", "aceite": "sim"})
    old, new = await _consents(app_sessions, team.tenant_id, team.corretor_id)
    assert (
        old.revoked_at is not None and new.phone_e164 == "+5511977776666" and new.revoked_at is None
    )
    panel.post("/painel/avisos", {"phone": "11 97777-6666"})
    assert "weekly_email" not in panel.get("/painel/avisos").text  # só o gestor tem e-mail semanal
    assert all(
        c.revoked_at is not None
        for c in await _consents(app_sessions, team.tenant_id, team.corretor_id)
    )


# --- Isolamento ---------------------------------------------------------------------------


async def test_other_tenant_is_invisible(
    panel: PanelClient, team: Team, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    other = await create_tenant(ops_sessions, "Concorrente")
    other_gestor = await _member(app_sessions, other, MemberRole.GESTOR)
    other_handoff = await _handoff(app_sessions, other)
    with panel_client(panel.app.state.settings, flush=False) as rival:
        rival.app.state.auth.clock = panel.clock
        rival.clock = panel.clock
        rival.login(other_gestor[1])
        _approve_sample(rival)
    async with tenant_session(app_sessions, other) as session:
        snapshot_id = await session.scalar(select(DataSnapshot.id))
    panel.login(team.gestor_email)
    assert panel.get(f"/painel/dados/{snapshot_id}").status_code == 404
    assert panel.post(f"/painel/passagens/{other_handoff}/assumir").status_code == 409
    assert panel.post("/painel/escolher", {"tenant_id": str(other)}).status_code == 403
    assert "+5511977776666" not in panel.get("/painel/passagens?show=todas").text
    assert panel.get("/painel/dados/nao-e-uuid").status_code == 404


async def test_manager_turns_weekly_email_off_without_consent(
    panel: PanelClient, team: Team, app_sessions: Sessions
) -> None:
    panel.login(team.gestor_email)
    assert 'name="weekly_email" value="1" checked' in panel.get("/painel/avisos").text
    panel.post("/painel/avisos", {"phone": ""})  # caixa desmarcada: desliga, sem pedir aceite
    async with tenant_session(app_sessions, team.tenant_id) as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == team.gestor_id)
        )
    assert member is not None and member.weekly_email_off_at is not None
    panel.post("/painel/avisos", {"phone": "", "weekly_email": "1"})
    async with tenant_session(app_sessions, team.tenant_id) as session:
        member = await session.scalar(
            select(TenantMember).where(TenantMember.user_id == team.gestor_id)
        )
    assert member is not None and member.weekly_email_off_at is None
