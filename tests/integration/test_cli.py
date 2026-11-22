"""Comandos de administração: operador e redefinição do autenticador."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.cli import CliError, create_demo_link, create_operator, main, reset_totp
from app.core.config import Settings
from app.core.rls import global_session
from app.models import AppUser
from tests.integration.panel_client import PanelClient, panel_client

pytestmark = pytest.mark.integration


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


def test_create_operator_then_accept_invite(panel: PanelClient) -> None:
    service = panel.app.state.auth
    link = panel.run(create_operator, service, " Op@Exemplo.com ", "Operadora")
    again = panel.run(create_operator, service, "op@exemplo.com", "Operadora")  # idempotente
    assert link != again
    path = again.removeprefix("https://localhost")
    assert panel.post(path, {"password": "girassol azul na varanda"}).status_code == 303
    assert panel.setup_totp().headers["location"] == "/operador"

    async def check() -> AppUser | None:
        async with global_session(service.db) as session:
            return await session.scalar(select(AppUser).where(AppUser.email == "op@exemplo.com"))

    user = panel.run(check)
    assert user is not None and user.is_operator and user.totp_secret_enc is not None

    panel.run(reset_totp, service, "op@exemplo.com")
    assert panel.get("/").headers["location"] == "/login"  # sessões encerradas
    assert panel.login_password("op@exemplo.com").headers["location"] == "/totp/cadastro"


def test_create_demo_link_opens_the_demo(panel: PanelClient, settings: Settings) -> None:
    output = panel.run(create_demo_link, panel.app.state.auth, "teste local", settings)
    url = output.splitlines()[-1]
    assert url.startswith("https://localhost/demo/entrar/")
    assert panel.get(url.removeprefix("https://localhost")).headers["location"] == "/demo"
    with pytest.raises(CliError):
        panel.run(create_demo_link, panel.app.state.auth, "   ", settings)


def test_invalid_input(panel: PanelClient) -> None:
    with pytest.raises(CliError):
        panel.run(create_operator, panel.app.state.auth, "sem-arroba", "x")
    with pytest.raises(CliError):
        panel.run(reset_totp, panel.app.state.auth, "ninguem@exemplo.com")


def test_main_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failing(_: object) -> str:
        raise CliError("x")

    monkeypatch.setattr("app.cli._run", failing)
    assert main(["reset-totp", "--email", "a@b.c"]) == 1
    assert "ERRO: x" in capsys.readouterr().err
