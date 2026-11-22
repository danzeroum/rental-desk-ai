"""Catálogos fictícios do kit de demonstração para vendas (RF25).

Tudo inventado: nomes, endereços, preços e fotos (domínio de exemplo). As imobiliárias
são marcadas `is_demo` e o painel e o simulador avisam que os dados são fictícios.
A pousada usa o mesmo domínio de casas, com os dados de quartos (ponto de extensão
anotado em DECISIONS: o rótulo "casa/quarto" ainda não é configurável).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.importer.model import BlockData, WorkbookData
from app.importer.sample import build_catalog


@dataclass(frozen=True)
class DemoAgency:
    name: str
    pnid: str
    """`phone_number_id` falso: o gateway de demonstração intercepta o prefixo `demo-`."""
    broker_name: str
    broker_phone: str
    """Telefone fictício do corretor (prefixo +55 00, que não existe no Brasil)."""
    unit_label: str
    """"casas" ou "quartos": só texto da tela de demonstração (ver DECISIONS, D046)."""
    suggestions: tuple[str, ...]

    @property
    def units(self) -> int:
        return len(_ROWS[self.pnid])

    def catalog(self, season_year: int) -> WorkbookData:
        builder = _BUILDERS[self.pnid]
        return builder(season_year)


# código, título, bairro, capacidade, quartos, suítes, banheiros, vagas,
# piscina, pet, festa, ar, wifi, churrasqueira, distância (m), diária base (R$)
_MARE_ALTA: tuple[tuple[object, ...], ...] = (
    ("MA01", "Casa Coral", "Praia Grande", 10, 4, 2, 3, 2,
     True, True, False, True, True, True, 120, 1600),
    ("MA02", "Casa Estrela-do-Mar", "Praia Grande", 8, 3, 1, 3, 2,
     True, False, False, True, True, True, 300, 1200),
    ("MA03", "Chalé Brisa", "Centro", 4, 2, 1, 1, 1,
     False, True, False, False, True, None, 700, 550),
    ("MA04", "Casa Maré Cheia", "Enseada", 12, 5, 3, 5, 3,
     True, False, True, True, True, True, 80, 2300),
    ("MA05", "Sobrado das Conchas", "Enseada", 6, 3, 1, 2, 1,
     False, True, False, True, True, True, 450, 900),
    ("MA06", "Casa Farol", "Ponta Alta", 14, 6, 4, 6, 4,
     True, False, True, True, True, True, 200, 2900),
    ("MA07", "Casa Areia Fina", "Centro", 6, 2, 1, 2, 1,
     True, False, False, None, True, False, 550, 850),
    ("MA08", "Casa Pôr do Sol", "Ponta Alta", 10, 4, 3, 4, 2,
     True, True, False, True, True, True, 150, 1900),
)  # fmt: skip

_RECANTO: tuple[tuple[object, ...], ...] = (
    ("Q01", "Quarto Jardim", "Térreo", 2, 1, 1, 1, 1,
     True, False, False, True, True, True, 60, 380),
    ("Q02", "Quarto Vista Mar", "1º andar", 2, 1, 1, 1, 1,
     True, False, False, True, True, True, 60, 520),
    ("Q03", "Quarto Família", "Térreo", 4, 1, 1, 1, 1,
     True, True, False, True, True, True, 60, 640),
    ("Q04", "Suíte Varanda", "1º andar", 3, 1, 1, 1, 1,
     True, False, False, True, True, True, 60, 590),
    ("Q05", "Quarto Duplo Simples", "Térreo", 2, 1, 1, 1, 0,
     True, False, False, False, True, True, 60, 300),
    ("Q06", "Chalé da Enseada", "Jardim", 5, 2, 1, 1, 1,
     True, True, False, True, True, True, 60, 780),
)  # fmt: skip

_MARE_RULES = {
    "check-in": "a partir das 15h",
    "check-out": "até as 11h",
    "caução": "R$ 800, devolvida após a vistoria",
    "pagamento": "40% na reserva e 60% na entrada, por Pix",
    "cancelamento": "devolução integral até 30 dias antes da entrada",
}
_RECANTO_RULES = {
    "check-in": "a partir das 14h",
    "check-out": "até as 12h",
    "café da manhã": "incluso, das 7h30 às 10h",
    "pagamento": "50% na reserva e 50% na chegada, por Pix ou cartão",
    "cancelamento": "devolução integral até 15 dias antes da entrada",
}


def _mare_alta(season_year: int) -> WorkbookData:
    n = season_year + 1
    return build_catalog(
        _MARE_ALTA, season_year, photos_base="https://exemplo.com.br/demo-mare-alta/fotos",
        rules=_MARE_RULES, notes="Dados fictícios de demonstração.",
        blocks=[
            BlockData("MA04", date(season_year, 12, 28), date(n, 1, 2)),
            BlockData("MA02", date(n, 1, 5), date(n, 1, 12)),
        ],
    )  # fmt: skip


def _recanto(season_year: int) -> WorkbookData:
    n = season_year + 1
    return build_catalog(
        _RECANTO, season_year, photos_base="https://exemplo.com.br/demo-recanto/fotos",
        rules=_RECANTO_RULES, notes="Dados fictícios de demonstração. Café da manhã incluso.",
        blocks=[BlockData("Q02", date(season_year, 12, 28), date(n, 1, 2))],
    )  # fmt: skip


_BUILDERS = {"demo-mare-alta": _mare_alta, "demo-recanto": _recanto}
_ROWS = {"demo-mare-alta": _MARE_ALTA, "demo-recanto": _RECANTO}

SALES_DEMOS: tuple[DemoAgency, ...] = (
    DemoAgency(
        "Imobiliária Maré Alta", "demo-mare-alta", "Rafa (corretor fictício)", "+550011110001",
        "casas",
        ("Casa para 8 pessoas de 28/12 a 02/01 com piscina",
         "Somos 4 de 15/01 a 20/01 e temos um cachorro", "Quero a primeira",
         "Quero falar com um corretor"),
    ),
    DemoAgency(
        "Pousada Recanto da Enseada", "demo-recanto", "Lu (recepção fictícia)", "+550011110002",
        "quartos",
        ("Quarto para 2 pessoas de 10/01 a 13/01", "Somos 2 adultos e 2 crianças de 20/12 a 23/12",
         "Quero a primeira", "Quero falar com um atendente"),
    ),
)  # fmt: skip
