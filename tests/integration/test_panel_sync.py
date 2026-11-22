"""Painel: calendários iCal e ligação da planilha Google."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.adapters.google_sheet import FakeSheetSource
from app.core.config import Settings
from app.core.crypto import Keyring
from app.core.rls import tenant_session
from app.importer.sample import build_sample
from app.importer.workbook import build_workbook
from app.models import CalendarFeed, SheetLink
from app.models.enums import MemberRole
from tests.integration.panel_client import PanelClient, panel_client
from tests.integration.test_panel_manager import _member
from tests.integration.test_pipeline import Sessions, setup_tenant

pytestmark = pytest.mark.integration
URL = "https://www.airbnb.com.br/calendar/ical/9.ics?s=token-secreto"
SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def keyring_of(settings: Settings) -> Keyring:
    return Keyring.parse(settings.data_encryption_keys.get_secret_value())


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


async def test_calendar_page(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions, settings: Settings
) -> None:
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring_of(settings))
    _, gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    _, corretor = await _member(app_sessions, tenant_id, MemberRole.CORRETOR)
    panel.login(gestor)
    assert panel.post("/painel/calendarios", {"codigo": "CASA99", "url": URL}).status_code == 400
    assert (
        panel.post("/painel/calendarios", {"codigo": "CASA01", "url": "airbnb"}).status_code == 400
    )
    assert panel.post("/painel/calendarios", {"codigo": "CASA01", "url": URL}).status_code == 303
    page = panel.get("/painel/calendarios").text
    assert "www.airbnb.com.br" in page and "token-secreto" not in page
    assert "falhou 1x" in page  # nos testes nada sai para a internet
    async with tenant_session(app_sessions, tenant_id) as session:
        feed = await session.scalar(select(CalendarFeed))
    assert feed is not None
    assert panel.post(f"/painel/calendarios/{feed.id}/remover").status_code == 303
    panel.post("/sair")
    panel.login(corretor)
    assert panel.get("/painel/calendarios").status_code == 403


async def test_google_sheet_link(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions, settings: Settings
) -> None:
    tenant_id, _ = await setup_tenant(app_sessions, ops_sessions, keyring_of(settings))
    _, gestor = await _member(app_sessions, tenant_id, MemberRole.GESTOR)
    panel.login(gestor)
    assert "ainda não foi configurada pelo operador" in panel.get("/painel/dados").text
    panel.app.state.sheets.source = FakeSheetSource({SHEET: build_workbook(build_sample(2026))})
    assert "rental-desk@projeto-teste.iam.gserviceaccount.com" in panel.get("/painel/dados").text
    bad = panel.post("/painel/planilha-google", {"link": "https://exemplo.com/x"})
    assert bad.status_code == 400
    link = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0"
    assert panel.post("/painel/planilha-google", {"link": link}).status_code == 303
    assert "em dia" in panel.get("/painel/dados").text
    async with tenant_session(app_sessions, tenant_id) as session:
        stored = await session.scalar(select(SheetLink))
    assert stored is not None and stored.sheet_id == SHEET and stored.last_success_at is not None
    assert panel.post("/painel/planilha-google/desligar").status_code == 303
    assert "Ligar planilha" in panel.get("/painel/dados").text
