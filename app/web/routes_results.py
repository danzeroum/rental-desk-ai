"""Painel de resultados (RF30): hoje, 7 dias e 30 dias, com a mesma conta do relatório."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from app.core.config import TIMEZONE
from app.domain.render import br_date, brl
from app.models import Tenant
from app.web.charts import HEIGHT, WIDTH, bar_chart, daily_counts
from app.web.common import render
from app.web.context import ManagerCtx
from app.workers.reports import collect_period

router = APIRouter(prefix="/painel")
PERIODS: Final = {"hoje": (1, "Hoje"), "7d": (7, "Últimos 7 dias"), "30d": (30, "Últimos 30 dias")}


@router.get("/resultados", response_class=HTMLResponse)
async def results(request: Request, ctx: ManagerCtx, periodo: str = "7d") -> Response:
    days, title = PERIODS.get(periodo, PERIODS["7d"])
    periodo = periodo if periodo in PERIODS else "7d"
    today = ctx.now().astimezone(TIMEZONE).date()
    first = today - timedelta(days=days - 1)
    async with ctx.db() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        data = await collect_period(session, tenant, (first, today + timedelta(days=1)))
    series = daily_counts(data.first_contacts, first, days)
    return render(
        request, "panel/results.html",
        {
            "m": data.metrics, "periodo": periodo, "periods": PERIODS, "title": title,
            "range": f"{br_date(first)} a {br_date(today)}",
            "value": brl(data.metrics.offered_value_cents),
            "chart": bar_chart(series, "contatos") if days > 1 else None, "series": series,
            "width": WIDTH, "height": HEIGHT,
        },
    )  # fmt: skip
