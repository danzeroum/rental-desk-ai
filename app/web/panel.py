"""Instala o painel na aplicação: arquivos estáticos, rotas, erros e cabeçalhos."""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.web import routes_auth
from app.web.common import SECURITY_HEADERS, WEB_DIR, LoginRequired, redirect, render

API_PREFIXES: Final = ("/webhook", "/health")
STATIC_CACHE: Final = "public, max-age=86400"
ERROR_HEADINGS: Final = {403: "Acesso negado", 404: "Página não encontrada"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = STATIC_CACHE
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


def _is_api(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIXES)


async def _login_required(request: Request, exc: Exception) -> Response:
    if request.headers.get("hx-request"):
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = "/login"
        return response
    return redirect("/login")


async def _http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, HTTPException):  # pragma: no cover - registrado só para HTTPException
        raise exc
    if _is_api(request):
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
    heading = ERROR_HEADINGS.get(exc.status_code, "Algo deu errado")
    shows_detail = isinstance(exc.detail, str) and exc.status_code != status.HTTP_404_NOT_FOUND
    message = exc.detail if shows_detail else ""
    return render(request, "error.html", {"heading": heading, "message": message}, exc.status_code)


def install_panel(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_exception_handler(LoginRequired, _login_required)
    app.add_exception_handler(HTTPException, _http_error)
    app.include_router(routes_auth.router)
