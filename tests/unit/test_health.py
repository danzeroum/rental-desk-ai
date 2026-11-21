from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api import health
from app.core.config import Settings
from app.main import create_app
from tests.conftest import production_settings


async def _ok(_: object) -> None:
    return None


async def _fail(_: object) -> None:
    raise ConnectionError("recusado")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_live_does_not_touch_dependencies(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "checks": {}}


def test_ready_ok_when_dependencies_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "ping_database", _ok)
    monkeypatch.setattr(health, "ping_redis", _ok)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok"}


def test_ready_503_without_leaking_error_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "ping_database", _fail)
    monkeypatch.setattr(health, "ping_redis", _ok)
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"] == {"postgres": "error", "redis": "ok"}
    assert "recusado" not in response.text


def test_trace_id_is_generated(client: TestClient) -> None:
    response = client.get("/health/live")
    assert len(response.headers["X-Request-ID"]) == 32


def test_valid_incoming_trace_id_is_kept(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123-def"})
    assert response.headers["X-Request-ID"] == "abc-123-def"


def test_invalid_incoming_trace_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "<script>"})
    assert response.headers["X-Request-ID"] != "<script>"


def test_docs_hidden_in_production() -> None:
    prod = production_settings()
    with TestClient(create_app(prod)) as test_client:
        assert test_client.get("/docs").status_code == 404
        assert test_client.get("/openapi.json").status_code == 404


def test_health_requests_are_logged_at_debug_only(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as test_client:
        test_client.get("/health/live")
        test_client.get("/nao-existe")
    out = capsys.readouterr().out
    assert '"path": "/health/live"' not in out
    assert '"path": "/nao-existe"' in out
