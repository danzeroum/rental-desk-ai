"""Rotas de acesso ao painel: login, TOTP, convite, nova senha e saída."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.core.security import provisioning_uri, qr_svg
from app.web.auth import AuthError, PanelSession, Stage
from app.web.common import (
    SESSION_COOKIE,
    auth,
    redirect,
    render,
    set_cookie,
    verify_csrf,
)

router = APIRouter()
ISSUER = "Rental Desk"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


async def _pending(request: Request, *stages: Stage) -> PanelSession | None:
    data = await auth(request).get_session(request.cookies.get(SESSION_COOKIE))
    return data if data is not None and data.stage in stages else None


def home_for(data: PanelSession) -> str:
    return "/operador" if data.is_operator and data.tenant_id is None else "/painel"


@router.get("/")
async def root(request: Request) -> Response:
    data = await _pending(request, Stage.ACTIVE)
    return redirect(home_for(data) if data else "/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    return render(request, "login.html")


@router.post("/login")
async def login(request: Request) -> Response:
    form = await verify_csrf(request)
    try:
        token, data = await auth(request).login(
            str(form.get("email", "")), str(form.get("password", "")), _client_ip(request)
        )
    except AuthError as exc:
        return render(request, "login.html", {"error": str(exc)}, status_code=400)
    response = redirect("/login/totp" if data.stage == Stage.PASSWORD else "/totp/cadastro")
    set_cookie(response, SESSION_COOKIE, token)
    return response


@router.get("/login/totp", response_class=HTMLResponse)
async def totp_page(request: Request) -> Response:
    if await _pending(request, Stage.PASSWORD) is None:
        return redirect("/login")
    return render(request, "totp.html")


@router.post("/login/totp")
async def totp_check(request: Request) -> Response:
    form = await verify_csrf(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    data = await _pending(request, Stage.PASSWORD)
    if data is None:
        return redirect("/login")
    try:
        data = await auth(request).check_totp(token, data, str(form.get("code", "")))
    except AuthError as exc:
        return render(request, "totp.html", {"error": str(exc)}, status_code=400)
    return redirect(await _after_login(request, token, data))


@router.get("/totp/cadastro", response_class=HTMLResponse)
async def totp_setup_page(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    data = await _pending(request, Stage.TOTP_SETUP)
    if data is None:
        return redirect("/login")
    service = auth(request)
    secret = await service.start_totp_setup(token, data)
    uri = provisioning_uri(secret, await service.email_of(data.user_uuid), issuer=ISSUER)
    return render(request, "totp_setup.html", {"qr": qr_svg(uri), "secret": secret})


@router.post("/totp/cadastro")
async def totp_setup(request: Request) -> Response:
    form = await verify_csrf(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    data = await _pending(request, Stage.TOTP_SETUP)
    if data is None:
        return redirect("/login")
    try:
        data = await auth(request).confirm_totp_setup(token, data, str(form.get("code", "")))
    except AuthError as exc:
        return render(
            request, "totp_setup.html", {"error": str(exc), "retry": True}, status_code=400
        )
    return redirect(await _after_login(request, token, data))


async def _after_login(request: Request, token: str, data: PanelSession) -> str:
    """Escolhe a imobiliária: única → direto; várias → lista; operador → área do operador."""
    memberships = await auth(request).memberships(data.user_uuid)
    if len(memberships) == 1 and not data.is_operator:
        await auth(request).choose_tenant(token, data, memberships[0].tenant_id)
        return "/painel"
    return "/operador" if data.is_operator else "/painel/escolher"


@router.post("/sair")
async def logout(request: Request) -> Response:
    await verify_csrf(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await auth(request).end_session(token)
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/convite/{token}", response_class=HTMLResponse)
async def invite_page(request: Request, token: str) -> Response:
    return render(
        request, "password_new.html", {"title": "Criar senha", "action": f"/convite/{token}"}
    )


@router.post("/convite/{token}")
async def accept_invite(request: Request, token: str) -> Response:
    form = await verify_csrf(request)
    try:
        session_token, _ = await auth(request).accept_invite(token, str(form.get("password", "")))
    except AuthError as exc:
        return render(
            request, "password_new.html",
            {"title": "Criar senha", "action": f"/convite/{token}", "error": str(exc)},
            status_code=400,
        )  # fmt: skip
    response = redirect("/totp/cadastro")
    set_cookie(response, SESSION_COOKIE, session_token)
    return response


@router.get("/senha/esqueci", response_class=HTMLResponse)
async def forgot_page(request: Request) -> Response:
    return render(request, "password_forgot.html")


@router.post("/senha/esqueci")
async def forgot(request: Request) -> Response:
    form = await verify_csrf(request)
    await auth(request).request_reset(str(form.get("email", "")))
    return render(request, "password_forgot.html", {"sent": True})


@router.get("/senha/nova/{token}", response_class=HTMLResponse)
async def reset_page(request: Request, token: str) -> Response:
    return render(
        request, "password_new.html", {"title": "Nova senha", "action": f"/senha/nova/{token}"}
    )


@router.post("/senha/nova/{token}")
async def reset(request: Request, token: str) -> Response:
    form = await verify_csrf(request)
    try:
        await auth(request).reset_password(token, str(form.get("password", "")))
    except AuthError as exc:
        return render(
            request, "password_new.html",
            {"title": "Nova senha", "action": f"/senha/nova/{token}", "error": str(exc)},
            status_code=400,
        )  # fmt: skip
    return render(request, "login.html", {"notice": "Senha alterada. Entre de novo."})
