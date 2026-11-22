"""Páginas de produto (RF31) e revisão de segurança automática das rotas (M11)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.core.config import Settings
from app.web.routes_public import HELP_ORDER
from tests.integration.panel_client import PanelClient, panel_client

pytestmark = pytest.mark.integration
PUBLIC = {
    "/", "/login", "/login/totp", "/totp/cadastro", "/sair", "/senha/esqueci", "/ajuda", "/termos",
    "/politica-de-privacidade", "/status", "/health", "/health/live",
}  # fmt: skip
PUBLIC_PREFIXES = ("/convite/", "/senha/nova/", "/privacidade/", "/ajuda/", "/webhooks/", "/static")


@pytest.fixture
def panel(settings: Settings) -> Iterator[PanelClient]:
    with panel_client(settings) as client:
        yield client


def _walk(routes: list[Any]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif (inner := getattr(route, "original_router", None)) is not None:
            yield from _walk(inner.routes)  # roteador incluído (FastAPI recente guarda aninhado)


def _routes(panel: PanelClient) -> list[tuple[str, str]]:
    found = []
    for route in _walk(panel.app.routes):
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", route.path)
        found += [(method, path) for method in route.methods or () if method in {"GET", "POST"}]
    return found


def test_help_center_and_product_pages(panel: PanelClient) -> None:
    index = panel.get("/ajuda")
    assert index.status_code == 200 and all(f'href="/ajuda/{s}"' in index.text for s in HELP_ORDER)
    for slug in HELP_ORDER:
        page = panel.get(f"/ajuda/{slug}")
        assert page.status_code == 200 and "<h1>" in page.text, slug
    assert "Content-Security-Policy" in panel.get("/ajuda/planilha").headers
    assert "<table>" in panel.get("/ajuda/planilha").text
    assert panel.get("/ajuda/../../etc/passwd").status_code == 404
    assert panel.get("/ajuda/inexistente").status_code == 404
    assert "revisado pelo advogado" in panel.get("/termos").text
    assert "operador" in panel.get("/politica-de-privacidade").text


def test_status_page_without_internal_details(panel: PanelClient) -> None:
    page = panel.get("/status")
    assert page.status_code in {200, 503}  # worker e scheduler não rodam neste teste
    assert "Banco de dados" in page.text and "funcionando" in page.text
    assert "postgres" not in page.text.lower() and "redis" not in page.text.lower()


def test_every_private_route_requires_login(panel: PanelClient) -> None:
    checked = 0
    for method, path in _routes(panel):
        if path in PUBLIC or path.startswith(PUBLIC_PREFIXES):
            continue
        response = panel.http.request(
            method, path, follow_redirects=False, data={"csrf": panel.csrf()}
        )
        assert response.status_code in {303, 403}, (method, path, response.status_code)
        if response.status_code == 303:
            assert response.headers["location"] == "/login", (method, path)
        checked += 1
    print(f"rotas privadas conferidas: {checked}")  # noqa: T201
    assert checked >= 40  # garante que a lista não ficou vazia por engano


def test_every_post_requires_csrf(panel: PanelClient) -> None:
    for method, path in _routes(panel):
        if method != "POST" or path.startswith("/webhooks/"):
            continue
        response = panel.http.post(path, data={"x": "1"}, follow_redirects=False)
        assert response.status_code in {303, 403}, (path, response.status_code)
        if response.status_code == 303:
            assert response.headers["location"] == "/login", path  # sem sessão, nem chega ao CSRF
