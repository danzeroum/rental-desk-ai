"""Dados fictícios da "Imobiliária Demo Riviera" (RF25): 10 casas para demonstração e testes.

As datas acompanham a temporada que começa em novembro de `season_year`; o
Carnaval é calculado a partir da Páscoa.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.importer.model import BlockData, PropertyData, RateData, WorkbookData

DEMO_TENANT_NAME = "Imobiliária Demo Riviera"
PHOTOS_BASE = "https://exemplo.com.br/demo-riviera/fotos"


def easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo gregoriano anônimo, de Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - nome da fórmula original
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def carnival_tuesday(year: int) -> date:
    return easter(year) - timedelta(days=47)


# código, título, módulo, capacidade, quartos, suítes, banheiros, vagas,
# piscina, pet, festa, ar, wifi, churrasqueira, distância, diária base (R$)
_HOUSES: tuple[tuple[object, ...], ...] = (
    ("CASA01", "Casa Pé na Areia", "Módulo 1", 12, 5, 3, 5, 3,
     True, False, False, True, True, True, 50, 2200),
    ("CASA02", "Casa do Jardim", "Módulo 2", 10, 4, 2, 4, 2,
     True, True, False, True, True, True, 300, 1500),
    ("CASA03", "Sobrado Azul", "Módulo 3", 8, 3, 1, 3, 2,
     False, True, None, True, True, True, 450, 1100),
    ("CASA04", "Casa das Palmeiras", "Módulo 5", 14, 6, 4, 6, 4,
     True, False, True, True, True, True, 200, 2800),
    ("CASA05", "Casa Vista Mar", "Módulo 6", 6, 3, 1, 2, 1,
     False, False, False, None, True, False, 100, 900),
    ("CASA06", "Casa da Figueira", "Módulo 7", 12, 5, 2, 4, 3,
     True, True, False, True, None, True, 600, 1700),
    ("CASA07", "Casa Recanto", "Módulo 8", 16, 7, 5, 7, 4,
     True, False, True, True, True, True, 350, 3200),
    ("CASA08", "Chalé das Gaivotas", "Módulo 10", 4, 2, 1, 1, 1,
     False, True, False, False, True, None, 800, 600),
    ("CASA09", "Casa Solar", "Módulo 12", 10, 4, 3, 4, 2,
     True, None, False, True, True, True, 250, 1900),
    ("CASA10", "Casa Maresia", "Módulo 15", 12, 5, 3, 5, 3,
     True, False, False, True, True, True, 150, 2400),
)  # fmt: skip

_RULES = {
    "check-in": "a partir das 14h",
    "check-out": "até as 11h",
    "caução": "R$ 1.000, devolvida após a vistoria",
    "pagamento": "50% na reserva e 50% na entrada, por Pix ou transferência",
    "cancelamento": "devolução integral até 30 dias antes da entrada",
}


def _property(
    row: tuple[object, ...],
    photos_base: str = PHOTOS_BASE,
    notes: str = "Roupa de cama e banho não incluídas.",
) -> PropertyData:
    (code, title, module, guests, bedrooms, suites, baths, parking,
     pool, pets, parties, ac, wifi, bbq, distance, _) = row  # fmt: skip
    return PropertyData(
        code=str(code),
        title=str(title),
        neighborhood=str(module),
        photos_url=f"{photos_base}/{str(code).lower()}",
        max_guests=int(str(guests)),
        bedrooms=int(str(bedrooms)),
        suites=int(str(suites)),
        bathrooms=int(str(baths)),
        parking_spots=int(str(parking)),
        has_pool=pool if isinstance(pool, bool) else None,
        accepts_pets=pets if isinstance(pets, bool) else None,
        allows_parties=parties if isinstance(parties, bool) else None,
        ac_in_bedrooms=ac if isinstance(ac, bool) else None,
        has_wifi=wifi if isinstance(wifi, bool) else None,
        has_barbecue=bbq if isinstance(bbq, bool) else None,
        beach_distance_m=int(str(distance)),
        public_notes=notes,
    )


def _rates(code: str, base_reais: int, index: int, season_year: int) -> list[RateData]:
    """Baixa, alta, Réveillon, janeiro, Carnaval e março, sem lacunas."""
    y, n = season_year, season_year + 1
    carnival = carnival_tuesday(n)
    carnival_in, carnival_out = carnival - timedelta(days=4), carnival + timedelta(days=1)
    closed = index % 2 == 0  # metade das casas só aluga o pacote completo
    base = base_reais * 100

    def rate(start: date, end: date, factor: float, min_nights: int = 2) -> RateData:
        return RateData(code, start, end, int(base * factor), min_nights, None, None, False, 30000)

    def package(start: date, end: date, name: str, nights_factor: float) -> RateData:
        nights = (end - start).days
        return RateData(
            code, start, end, int(base * 2), nights, name,
            int(base * 2 * nights * nights_factor), closed, 30000,
        )  # fmt: skip

    return [
        rate(date(y, 11, 1), date(y, 12, 20), 1.0),
        rate(date(y, 12, 20), date(y, 12, 28), 1.5, 3),
        package(date(y, 12, 28), date(n, 1, 2), "Réveillon", 1.0),
        rate(date(n, 1, 2), carnival_in, 1.4, 3),
        package(carnival_in, carnival_out, "Carnaval", 0.9),
        rate(carnival_out, date(n, 4, 1), 1.0),
    ]


def build_catalog(
    rows: tuple[tuple[object, ...], ...],
    season_year: int,
    *,
    photos_base: str,
    rules: dict[str, str],
    notes: str,
    blocks: list[BlockData],
) -> WorkbookData:
    """Catálogo fictício completo (planilha aprovável) a partir das linhas de unidades."""
    return WorkbookData(
        properties=[_property(row, photos_base, notes) for row in rows],
        rates=[
            rate
            for index, row in enumerate(rows)
            for rate in _rates(str(row[0]), int(str(row[-1])), index, season_year)
        ],
        blocks=blocks,
        rules=dict(rules),
    )


def build_sample(season_year: int) -> WorkbookData:
    n = season_year + 1
    blocks = [
        BlockData("CASA03", date(season_year, 12, 28), date(n, 1, 2)),
        BlockData("CASA05", date(n, 1, 10), date(n, 1, 17)),
        BlockData("CASA09", date(season_year, 12, 20), date(season_year, 12, 27)),
    ]
    return build_catalog(
        _HOUSES, season_year, photos_base=PHOTOS_BASE, rules=_RULES,
        notes="Roupa de cama e banho não incluídas.", blocks=blocks,
    )  # fmt: skip
