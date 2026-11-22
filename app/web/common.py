"""Peças comuns do painel: templates, CSRF, sessão exigida e cabeçalhos de segurança."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.core.security import new_token
from app.web.auth import AuthService, PanelSession, Stage

SESSION_COOKIE: Final = "rd_session"
CSRF_COOKIE: Final = "rd_csrf"
CSRF_FIELD: Final = "csrf"
CSRF_HEADER: Final = "x-csrf-token"
WEB_DIR: Final = Path(__file__).parent

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

SECURITY_HEADERS: Final = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class LoginRequired(Exception):  # noqa: N818 - fluxo, não erro
    """Sem sessão ativa: redireciona para o login."""


def auth(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


def csrf_token(request: Request) -> str:
    """Token do cookie, ou um novo (gravado na resposta por `render`)."""
    token = request.cookies.get(CSRF_COOKIE) or getattr(request.state, "new_csrf", None)
    if token is None:
        token = new_token()
        request.state.new_csrf = token
    return token


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200
) -> HTMLResponse:
    values = {"csrf": csrf_token(request), "session": getattr(request.state, "panel", None)}
    values.update(context or {})
    response = templates.TemplateResponse(request, name, values, status_code=status_code)
    if getattr(request.state, "new_csrf", None):
        set_cookie(response, CSRF_COOKIE, request.state.new_csrf, http_only=False)
    return response


def set_cookie(response: Response, name: str, value: str, *, http_only: bool = True) -> None:
    response.set_cookie(
        name, value, httponly=http_only, secure=True, samesite="lax", path="/", max_age=8 * 3600
    )


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


async def verify_csrf(request: Request) -> dict[str, Any]:
    """Confere origem e token (duplo envio: cookie + campo ou cabeçalho). Devolve o formulário."""
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "origem não permitida")
    form = dict(await request.form())
    sent = request.headers.get(CSRF_HEADER) or str(form.get(CSRF_FIELD, ""))
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not hmac.compare_digest(sent, cookie):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "formulário expirado; recarregue a página")
    return form


async def panel_session(request: Request) -> PanelSession:
    """Sessão ativa (senha + TOTP conferidos); senão, login."""
    data = await auth(request).get_session(request.cookies.get(SESSION_COOKIE))
    if data is None or data.stage != Stage.ACTIVE:
        raise LoginRequired
    request.state.panel = data
    return data


def require_tenant(data: PanelSession) -> PanelSession:
    if data.tenant_uuid is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "escolha uma imobiliária")
    return data


def require_operator(data: PanelSession) -> PanelSession:
    if not data.is_operator:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "área do operador")
    return data


def safe_next(value: object, default: str) -> str:
    """Só caminhos internos do painel (evita redirecionamento aberto)."""
    text = str(value or "")
    return text if text.startswith("/painel") and "//" not in text and "\\" not in text else default
