"""Gráfico de barras em SVG desenhado no servidor (RF30). Puro: só geometria.

Uma série, uma cor (token `--serie`), barras finas com 2 px de folga, ponta superior
arredondada (4 px) e base reta no eixo; dica nativa (`<title>`) em cada barra; a tabela
equivalente vai junto na página. Sem JavaScript (CSP `script-src 'self'`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from app.core.config import TIMEZONE

WIDTH: Final = 1000
HEIGHT: Final = 220
TOP, BOTTOM, LEFT = 16, 24, 32
GAP: Final = 2
RADIUS: Final = 4


@dataclass(frozen=True)
class Bar:
    day: date
    value: int
    path: str
    label: str


@dataclass(frozen=True)
class BarChart:
    bars: list[Bar]
    max_value: int
    baseline_y: float
    ticks: list[tuple[float, str, str]]
    """Rótulos do eixo x (posição, texto, âncora): primeiro, meio e último dia; as pontas
    ancoram para dentro, para não sair do desenho."""
    gridline_y: float


def daily_counts(moments: Sequence[datetime], first_day: date, days: int) -> list[tuple[date, int]]:
    counts = Counter(m.astimezone(TIMEZONE).date() for m in moments)
    return [
        (first_day + timedelta(days=i), counts.get(first_day + timedelta(days=i), 0))
        for i in range(days)
    ]


def _nice_max(value: int) -> int:
    """Topo do eixo arredondado para cima: 1, 2, 2,5 ou 5 vezes uma potência de 10."""
    if value <= 5:  # noqa: PLR2004
        return max(value, 1)
    magnitude: int = 10 ** int(math.log10(value))
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if step * magnitude >= value:
            return math.ceil(step * magnitude)
    return 10 * magnitude  # pragma: no cover - o passo 10 sempre cobre


def _bar_path(x: float, y: float, w: float, h: float) -> str:
    r = min(RADIUS, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z"
    )


def bar_chart(series: Sequence[tuple[date, int]], unit: str) -> BarChart:
    top_value = _nice_max(max((v for _, v in series), default=0))
    plot_h = HEIGHT - TOP - BOTTOM
    slot = (WIDTH - LEFT) / max(len(series), 1)
    width = max(slot - GAP, 1)
    bars = []
    for index, (day, value) in enumerate(series):
        h = plot_h * value / top_value
        x, y = LEFT + index * slot, TOP + plot_h - h
        bars.append(
            Bar(
                day,
                value,
                _bar_path(x, y, width, h) if value else "",
                f"{day:%d/%m}: {value} {unit}",
            )
        )
    picks = sorted({0, len(series) // 2, len(series) - 1}) if series else []
    last = len(series) - 1
    ticks = [
        (
            LEFT + i * slot + (0 if i == 0 else width if i == last else width / 2),
            f"{series[i][0]:%d/%m}",
            "start" if i == 0 else "end" if i == last else "middle",
        )
        for i in picks
    ]
    return BarChart(bars, top_value, TOP + plot_h, ticks, TOP)
