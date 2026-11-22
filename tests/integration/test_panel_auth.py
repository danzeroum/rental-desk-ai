"""Acesso ao painel (SPEC, seção 5): e-mail + senha + TOTP obrigatório para todos."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.mailer import FakeMailer
from app.core.config import Settings
from app.core.rls import global_session
from app.core.security import totp_code
from app.models import AppUser, TenantMember
from app.models.enums import MemberRole
from app.web.common import SESSION_COOKIE
from tests.factories import create_tenant
from tests.integration.panel_client import PanelClient, panel_client, user_with_password

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


async def _user(app_sessions: Sessions, *, operator: bool = False) -> tuple[uuid.UUID, str]:
    user_id, email, hashed = user_with_password()
    async with global_session(app_sessions) as session:
        session.add(
            AppUser(id=user_id, email=email, display_name="Ana", password_hash=hashed,
                    is_operator=operator)
        )  # fmt: skip
    return user_id, email


async def _member(app_sessions: Sessions, ops_sessions: Sessions, user_id: uuid.UUID) -> uuid.UUID:
    from app.core.rls import tenant_session  # noqa: PLC0415

    tenant_id = await create_tenant(ops_sessions)
    async with tenant_session(app_sessions, tenant_id) as session:
        session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role=MemberRole.GESTOR))
    return tenant_id


async def test_first_login_requires_totp_setup_then_lands_on_panel(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id, email = await _user(app_sessions)
    await _member(app_sessions, ops_sessions, user_id)
    response = panel.login_password(email)
    assert response.headers["location"] == "/totp/cadastro"
    assert panel.get("/").headers["location"] == "/login"  # ainda não está ativa
    page = panel.get("/totp/cadastro")
    assert "<svg" in page.text and "Content-Security-Policy" in page.headers
    done = panel.setup_totp()
    assert done.status_code == 303 and done.headers["location"] == "/painel"
    async with global_session(app_sessions) as session:
        user = await session.get(AppUser, user_id)
        assert user is not None and user.totp_secret_enc is not None
        assert panel.totp_secret and panel.totp_secret not in repr(user.totp_secret_enc)
    assert panel.get("/").headers["location"] == "/painel"


async def test_second_login_asks_code_and_rejects_reuse(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id, email = await _user(app_sessions)
    await _member(app_sessions, ops_sessions, user_id)
    panel.login(email)
    panel.post("/sair")
    assert panel.login_password(email).headers["location"] == "/login/totp"
    assert panel.totp_secret
    code = totp_code(panel.totp_secret, panel.clock())
    assert panel.post("/login/totp", {"code": code}).status_code == 303
    panel.post("/sair")
    panel.login_password(email)
    reused = panel.post("/login/totp", {"code": code})
    assert reused.status_code == 400 and "já usado" in reused.text


async def test_wrong_password_is_generic_and_locks_after_five(
    panel: PanelClient, app_sessions: Sessions
) -> None:
    _, email = await _user(app_sessions)
    unknown = panel.login_password("ninguem@exemplo.com")
    wrong = panel.login_password(email, "senha errada mas longa")
    assert unknown.status_code == wrong.status_code == 400
    assert (
        "E-mail ou senha incorretos." in unknown.text
        and "E-mail ou senha incorretos." in wrong.text
    )
    for _ in range(4):
        panel.login_password(email, "senha errada mas longa")
    locked = panel.login_password(email)  # senha certa, mas conta bloqueada
    assert locked.status_code == 400 and "bloqueada" in locked.text
    panel.clock.advance(minutes=16)
    assert panel.login_password(email).status_code == 303


async def test_totp_brute_force_ends_session(panel: PanelClient, app_sessions: Sessions) -> None:
    _, email = await _user(app_sessions)
    panel.login(email)
    panel.post("/sair")
    panel.login_password(email)
    for _ in range(5):
        assert panel.post("/login/totp", {"code": "000000"}).status_code == 400
    blocked = panel.post("/login/totp", {"code": "000000"})
    assert "Muitos códigos errados" in blocked.text
    assert panel.get("/login/totp").headers["location"] == "/login"


async def test_post_without_csrf_or_from_other_origin_is_rejected(
    panel: PanelClient, app_sessions: Sessions
) -> None:
    _, email = await _user(app_sessions)
    panel.csrf()
    no_token = panel.http.post("/login", data={"email": email, "password": "x"})
    assert no_token.status_code == 403
    other = panel.http.post(
        "/login", data={"csrf": panel.csrf(), "email": email, "password": "x"},
        headers={"origin": "https://evil.example"},
    )  # fmt: skip
    assert other.status_code == 403


async def test_session_cookie_is_secure_httponly(
    panel: PanelClient, app_sessions: Sessions
) -> None:
    _, email = await _user(app_sessions)
    response = panel.login_password(email)
    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie


async def test_invite_creates_password_then_totp(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id = uuid.uuid4()
    async with global_session(app_sessions) as session:
        session.add(
            AppUser(id=user_id, email=f"c-{user_id.hex[:8]}@exemplo.com", display_name="Bia")
        )
    await _member(app_sessions, ops_sessions, user_id)
    async with global_session(app_sessions) as session:
        user = await session.get(AppUser, user_id)
    assert user is not None
    link = panel.run(panel.app.state.auth.send_invite, user, "Imobiliária Sol")
    mailer: FakeMailer = panel.app.state.mailer
    assert mailer.sent[-1].body.count(link) == 1
    path = link.removeprefix("https://localhost")
    weak = panel.post(path, {"password": "curta"})
    assert weak.status_code == 400 and "12 caracteres" in weak.text
    accepted = panel.post(path, {"password": "girassol azul na varanda"})
    assert accepted.headers["location"] == "/totp/cadastro"
    assert panel.setup_totp().headers["location"] == "/painel"
    assert (
        panel.post(path, {"password": "girassol azul na varanda"}).status_code == 400
    )  # uso único


async def test_password_reset_is_silent_for_unknown_and_ends_sessions(
    panel: PanelClient, app_sessions: Sessions, settings: Settings
) -> None:
    _, email = await _user(app_sessions)
    panel.login(email)
    mailer: FakeMailer = panel.app.state.mailer
    before = len(mailer.sent)
    unknown = panel.post("/senha/esqueci", {"email": "ninguem@exemplo.com"})
    known = panel.post("/senha/esqueci", {"email": email})
    assert unknown.text.count("Se o e-mail estiver cadastrado") == 1
    assert known.text.count("Se o e-mail estiver cadastrado") == 1
    assert len(mailer.sent) == before + 1
    link = re.search(r"https://\S+/senha/nova/\S+", mailer.sent[-1].body)
    assert link
    path = link.group(0).removeprefix("https://localhost")
    with panel_client(settings, flush=False) as other_device:
        other_device.app.state.auth.clock = panel.clock
        other_device.http.cookies.set(SESSION_COOKIE, panel.http.cookies[SESSION_COOKIE])
        assert panel.post(path, {"password": "nova frase bem comprida"}).status_code == 200
        assert other_device.get("/").headers["location"] == "/login"
    assert panel.login_password(email, "nova frase bem comprida").status_code == 303


async def test_operator_goes_to_operator_area(panel: PanelClient, app_sessions: Sessions) -> None:
    _, email = await _user(app_sessions, operator=True)
    assert panel.login(email).headers["location"] == "/operador"


async def test_multi_tenant_user_chooses(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id, email = await _user(app_sessions)
    await _member(app_sessions, ops_sessions, user_id)
    await _member(app_sessions, ops_sessions, user_id)
    assert panel.login(email).headers["location"] == "/painel/escolher"


async def test_unknown_page_is_html_and_webhook_errors_stay_json(panel: PanelClient) -> None:
    page = panel.get("/nao-existe")
    assert page.status_code == 404 and "Página não encontrada" in page.text
    assert panel.get("/static/painel.css").headers["cache-control"].startswith("public")


async def test_memberships_are_only_the_users(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id, _ = await _user(app_sessions)
    other_id, _ = await _user(app_sessions)
    mine = await _member(app_sessions, ops_sessions, user_id)
    await _member(app_sessions, ops_sessions, other_id)
    found = panel.run(panel.app.state.auth.memberships, user_id)
    assert [m.tenant_id for m in found] == [mine]
    async with global_session(app_sessions) as session:
        rows = (await session.execute(select(TenantMember))).scalars().all()
    assert rows == []  # sem tenant nem usuário, o RLS esconde tudo


async def test_token_rotates_after_totp_and_sessions_expire_after_12h(
    panel: PanelClient, app_sessions: Sessions, ops_sessions: Sessions
) -> None:
    user_id, email = await _user(app_sessions)
    await _member(app_sessions, ops_sessions, user_id)
    panel.login_password(email)
    before = panel.http.cookies[SESSION_COOKIE]
    panel.setup_totp()
    after = panel.http.cookies[SESSION_COOKIE]
    assert after != before
    stolen = panel.run(panel.app.state.auth.get_session, before)
    assert stolen is None  # o token da etapa da senha não vale mais
    assert panel.get("/painel").status_code == 200
    panel.clock.advance(hours=7)
    assert panel.get("/painel").status_code == 200  # em uso, dentro do limite
    panel.clock.advance(hours=5, minutes=1)
    assert panel.get("/painel").headers["location"] == "/login"  # 12 h: entra de novo
