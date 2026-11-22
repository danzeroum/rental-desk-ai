"""Kit de demonstração para vendas: conversa com aparência de WhatsApp + celular do corretor.

Sem login de painel: o acesso é por link com token (`/demo/entrar/<token>`), criado e
revogado pelo operador em `/operador/demo`. Mesmo pipeline do produto, em números `demo-…`
que nunca saem para o WhatsApp.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from markupsafe import Markup, escape

from app.importer.sales_demo import SALES_DEMOS, DemoAgency
from app.web.auth import PanelSession
from app.web.common import local_time, redirect, render, templates, verify_csrf
from app.web.routes_operator import Operator
from app.workers.sales_demo_chat import (
    VISIT_TTL,
    DemoAccessError,
    SalesDemoChat,
    Visit,
    agency_by_pnid,
)

router = APIRouter()
DEMO_COOKIE: Final = "rd_demo"
NOINDEX: Final = {"X-Robots-Tag": "noindex, nofollow"}
_BOLD: Final = re.compile(r"\*([^*\n]+)\*")


def whatsapp_text(text: str) -> Markup:
    """Negrito do WhatsApp (*texto*) com escape de HTML; as quebras ficam no CSS.

    Só literais viram HTML: cada trecho do texto passa por `escape` (dentro ou fora do negrito).
    """
    parts = _BOLD.split(text)  # pares = texto comum; ímpares = dentro de *...*
    return Markup("").join(
        Markup("<strong>{}</strong>").format(part) if index % 2 else escape(part)
        for index, part in enumerate(parts)
    )


templates.env.filters["whatsapp"] = whatsapp_text


def chat(request: Request) -> SalesDemoChat:
    service: SalesDemoChat = request.app.state.sales_demo
    return service


async def current_visit(request: Request) -> Visit:
    visit = await chat(request).visit(request.cookies.get(DEMO_COOKIE))
    if visit is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "link expirado ou revogado")
    return visit


VisitDep = Annotated[Visit, Depends(current_visit)]


def _agency(pnid: str) -> DemoAgency:
    agency = agency_by_pnid(pnid)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return agency


def _page(request: Request, name: str, context: dict[str, object], code: int = 200) -> Response:
    response = render(request, name, context, code)
    response.headers.update(NOINDEX)
    return response


@router.get("/demo/entrar/{token}")
async def enter(request: Request, token: str) -> Response:
    cookie = await chat(request).start_visit(token)
    if cookie is None:
        return _page(request, "demo/invalid.html", {}, status.HTTP_403_FORBIDDEN)
    response = redirect("/demo")
    response.set_cookie(
        DEMO_COOKIE, cookie, httponly=True, secure=True, samesite="lax", path="/demo",
        max_age=int(VISIT_TTL.total_seconds()),
    )  # fmt: skip
    response.headers.update(NOINDEX)
    return response


@router.get("/demo", response_class=HTMLResponse)
async def choose(request: Request, visit: VisitDep) -> Response:
    return _page(request, "demo/choose.html", {"agencies": SALES_DEMOS})


@router.get("/demo/{pnid}", response_class=HTMLResponse)
async def conversation(request: Request, pnid: str, visit: VisitDep) -> Response:
    agency = _agency(pnid)
    view = await chat(request).view(visit, agency)
    return _page(request, "demo/chat.html", {"agency": agency, "view": view, "visit": visit})


@router.get("/demo/{pnid}/telas", response_class=HTMLResponse)
async def panes(request: Request, pnid: str, visit: VisitDep) -> Response:
    """Fragmento atualizado pelo HTMX a cada 1,5 s."""
    agency = _agency(pnid)
    view = await chat(request).view(visit, agency)
    return _page(request, "demo/_panes.html", {"agency": agency, "view": view, "visit": visit})


async def _act(request: Request, pnid: str, visit: Visit, action: str) -> Response:
    form = await verify_csrf(request)
    agency, service, error = _agency(pnid), chat(request), None
    try:
        if action == "enviar":
            await service.say(visit, agency, str(form.get("text", "")))
        elif action == "assumir":
            await service.broker_assume(visit, agency)
        else:
            visit = await service.restart(visit)
    except DemoAccessError as exc:
        error = str(exc)
    if not request.headers.get("hx-request"):
        return redirect(f"/demo/{agency.pnid}")
    view = await service.view(visit, agency)
    context = {"agency": agency, "view": view, "visit": visit, "error": error}
    return _page(request, "demo/_panes.html", context)


@router.post("/demo/{pnid}/enviar")
async def send(request: Request, pnid: str, visit: VisitDep) -> Response:
    return await _act(request, pnid, visit, "enviar")


@router.post("/demo/{pnid}/assumir")
async def assume(request: Request, pnid: str, visit: VisitDep) -> Response:
    return await _act(request, pnid, visit, "assumir")


@router.post("/demo/{pnid}/recomecar")
async def restart(request: Request, pnid: str, visit: VisitDep) -> Response:
    return await _act(request, pnid, visit, "recomecar")


# --- Operador: criar e revogar links ---------------------------------------------------


async def _links_page(
    request: Request, operator: PanelSession, extra: dict[str, object] | None = None,
    code: int = 200,
) -> Response:  # fmt: skip
    links = await chat(request).links()
    context = {"links": links, "fmt": local_time, **(extra or {})}
    return render(request, "operator/demo_links.html", context, code)


@router.get("/operador/demo", response_class=HTMLResponse)
async def links_page(request: Request, operator: Operator) -> Response:
    return await _links_page(request, operator)


@router.post("/operador/demo")
async def create_link(request: Request, operator: Operator) -> Response:
    form = await verify_csrf(request)
    try:
        token, _ = await chat(request).create_link(str(form.get("label", "")), operator.user_uuid)
    except DemoAccessError as exc:
        return await _links_page(request, operator, {"error": str(exc)}, 400)
    base = request.app.state.settings.public_base_url.rstrip("/")
    return await _links_page(request, operator, {"new_link": f"{base}/demo/entrar/{token}"})


@router.post("/operador/demo/{link_id}/revogar")
async def revoke_link(request: Request, link_id: uuid.UUID, operator: Operator) -> Response:
    await verify_csrf(request)
    await chat(request).revoke(link_id, operator.user_uuid)
    return await _links_page(request, operator, {"notice": "Link revogado: deixou de abrir."})
