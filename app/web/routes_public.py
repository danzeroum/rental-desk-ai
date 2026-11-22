"""Páginas de produto (RF31): central de ajuda, termos, política de privacidade e status.

Públicas (sem login). Os textos ficam em `app/web/content` (Markdown) e são lidos uma vez.
A página de status diz só se cada parte está funcionando, sem detalhe interno.
"""

from __future__ import annotations

import asyncio
from functools import cache
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from app.api.health import _check
from app.core.db import ping_database
from app.core.redis import ping_redis
from app.web.common import WEB_DIR, render
from app.web.markdown import title_of, to_html

router = APIRouter()
CONTENT: Final = WEB_DIR / "content"
HELP_ORDER: Final = ("primeiros-passos", "planilha", "calendarios", "passagens", "perguntas")
PAGES: Final = {"termos": "termos.md", "politica-de-privacidade": "politica-de-privacidade.md"}


@cache
def _page(path: Path) -> tuple[str, str]:
    source = path.read_text(encoding="utf-8")
    return title_of(source), to_html(source)


def _help_pages() -> list[tuple[str, str]]:
    return [(slug, _page(CONTENT / "ajuda" / f"{slug}.md")[0]) for slug in HELP_ORDER]


@router.get("/ajuda", response_class=HTMLResponse)
async def help_index(request: Request) -> Response:
    return render(request, "public/help_index.html", {"pages": _help_pages()})


@router.get("/ajuda/{slug}", response_class=HTMLResponse)
async def help_page(request: Request, slug: str) -> Response:
    if slug not in HELP_ORDER:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    title, body = _page(CONTENT / "ajuda" / f"{slug}.md")
    return render(
        request,
        "public/page.html",
        {"title": title, "body": body, "back": "/ajuda", "pages": _help_pages()},
    )


@router.get("/termos", response_class=HTMLResponse)
@router.get("/politica-de-privacidade", response_class=HTMLResponse)
async def product_page(request: Request) -> Response:
    title, body = _page(CONTENT / PAGES[request.url.path.strip("/")])
    return render(
        request, "public/page.html", {"title": title, "body": body, "back": None, "pages": None}
    )


async def _service_alive(redis: Any, service: str) -> str:
    try:
        async for _ in redis.scan_iter(match=f"heartbeat:{service}:*", count=50):
            return "ok"
    except Exception:
        return "error"
    return "error"


@router.get("/status", response_class=HTMLResponse)
async def status_page(request: Request) -> Response:
    state = request.app.state
    timeout = state.settings.health_timeout_seconds
    db, cache_ok, worker, scheduler = await asyncio.gather(
        _check("postgres", lambda: ping_database(state.db_engine), timeout),
        _check("redis", lambda: ping_redis(state.redis), timeout),
        _service_alive(state.redis, "worker"),
        _service_alive(state.redis, "scheduler"),
    )
    parts = [
        ("Painel e recebimento de mensagens", "ok"),
        ("Banco de dados", db),
        ("Fila de atendimento", cache_ok),
        ("Respostas aos hóspedes", worker),
        ("Lembretes, avisos e sincronizações", scheduler),
    ]
    healthy = all(value == "ok" for _, value in parts)
    return render(
        request,
        "public/status.html",
        {"parts": parts, "healthy": healthy},
        status_code=200 if healthy else 503,
    )
