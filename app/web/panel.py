"""Instala o painel na aplicação: arquivos estáticos, rotas, erros e cabeçalhos."""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.web import (
    routes_auth,
    routes_data,
    routes_onboarding,
    routes_operator,
    routes_panel,
    routes_privacy,
    routes_results,
    routes_settings,
    routes_shadow,
    routes_sync,
)
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
    if status.HTTP_300_MULTIPLE_CHOICES <= exc.status_code < status.HTTP_400_BAD_REQUEST:
        return redirect((exc.headers or {}).get("Location", "/"))
    if _is_api(request):
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
    heading = ERROR_HEADINGS.get(exc.status_code, "Algo deu errado")
    shows_detail = isinstance(exc.detail, str) and exc.status_code != status.HTTP_404_NOT_FOUND
    message = exc.detail if shows_detail else ""
    return render(request, "error.html", {"heading": heading, "message": message}, exc.status_code)


async def _invalid_request(request: Request, exc: Exception) -> Response:
    """Endereço malformado no painel (ex.: id inválido) vira 404; a API mantém o 422."""
    if _is_api(request) or not isinstance(exc, RequestValidationError):
        return await request_validation_exception_handler(request, exc)  # type: ignore[arg-type]
    heading = ERROR_HEADINGS[status.HTTP_404_NOT_FOUND]
    return render(
        request, "error.html", {"heading": heading, "message": ""}, status.HTTP_404_NOT_FOUND
    )


def install_panel(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_exception_handler(LoginRequired, _login_required)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    for module in (
        routes_auth,
        routes_panel,
        routes_data,
        routes_settings,
        routes_operator,
        routes_privacy,
        routes_shadow,
        routes_sync,
        routes_results,
        routes_onboarding,
    ):
        app.include_router(module.router)
