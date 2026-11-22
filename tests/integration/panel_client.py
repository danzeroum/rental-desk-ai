"""Cliente do painel para testes: CSRF, login com senha + TOTP e relógio controlado."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.security import hash_password, totp_code
from app.main import create_app
from app.web.common import CSRF_COOKIE

BASE_URL = "https://testserver"
PASSWORD = "girassol azul na varanda"


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 11, 23, 13, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class PanelClient:
    def __init__(self, client: TestClient, clock: Clock) -> None:
        self.http, self.clock = client, clock
        self.totp_secret: str | None = None

    @property
    def app(self) -> FastAPI:
        app: FastAPI = self.http.app  # type: ignore[assignment]
        return app

    def run(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """Executa uma corrotina no laço de eventos da aplicação."""
        portal = self.http.portal
        assert portal is not None
        return portal.call(fn, *args)

    def csrf(self) -> str:
        token = self.http.cookies.get(CSRF_COOKIE)
        if token is None:
            self.http.get("/login")
            token = self.http.cookies.get(CSRF_COOKIE)
        assert token
        return token

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.http.get(url, follow_redirects=False, **kwargs)

    def post(self, url: str, data: dict[str, Any] | None = None, **kwargs: Any) -> Response:
        form = {"csrf": self.csrf(), **(data or {})}
        return self.http.post(url, data=form, follow_redirects=False, **kwargs)

    def login_password(self, email: str, password: str = PASSWORD) -> Response:
        return self.post("/login", {"email": email, "password": password})

    def setup_totp(self) -> Response:
        page = self.get("/totp/cadastro")
        found = re.search(r'<code class="chave">([A-Z2-7]+)</code>', page.text)
        assert found, page.text
        self.totp_secret = found.group(1)
        return self.post("/totp/cadastro", {"code": totp_code(self.totp_secret, self.clock())})

    def login(self, email: str, totp_secret: str | None = None) -> Response:
        """Login completo; avança o relógio um passo para não repetir código."""
        response = self.login_password(email)
        assert response.status_code == 303, response.text
        if response.headers["location"] == "/totp/cadastro":
            return self.setup_totp()
        self.clock.advance(seconds=30)
        secret = totp_secret or self.totp_secret
        assert secret
        return self.post("/login/totp", {"code": totp_code(secret, self.clock())})


@contextmanager
def panel_client(settings: Any, *, flush: bool = True) -> Iterator[PanelClient]:
    clock = Clock()
    with TestClient(create_app(settings), base_url=BASE_URL) as client:
        client.app.state.auth.clock = clock  # type: ignore[attr-defined]
        panel = PanelClient(client, clock)
        if flush:
            panel.run(client.app.state.redis.flushdb)  # type: ignore[attr-defined]
        yield panel


def user_with_password(email_prefix: str = "u") -> tuple[uuid.UUID, str, str]:
    user_id = uuid.uuid4()
    return user_id, f"{email_prefix}-{user_id.hex[:10]}@exemplo.com", hash_password(PASSWORD)
