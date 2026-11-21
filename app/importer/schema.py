"""Planilha modelo (SPEC, seção 8): abas, colunas e tipos.

Fonte única usada pelo gerador do arquivo modelo, pelo leitor e pelo validador.
Os nomes das abas e colunas são o "contrato de dados" com a imobiliária.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Kind(StrEnum):
    CODE = "code"
    TEXT = "text"
    URL = "url"
    INT = "int"
    BOOL = "bool"
    DATE = "date"
    MONEY = "money"


@dataclass(frozen=True)
class Column:
    name: str
    kind: Kind
    required: bool
    help: str
    max_length: int = 120
    min_value: int = 0


@dataclass(frozen=True)
class Sheet:
    name: str
    columns: tuple[Column, ...]
    max_rows: int
    help: str

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


def _attr(name: str, label: str) -> Column:
    return Column(name, Kind.BOOL, False, f"{label}? Sim, Não ou vazio (vazio = não sabemos).")


IMOVEIS: Final = Sheet(
    name="Imoveis",
    max_rows=1000,
    help="Uma linha por casa. O código liga esta aba às outras.",
    columns=(
        Column(
            "codigo",
            Kind.CODE,
            True,
            "Código único da casa, ex.: CASA12. Letras, números, - ou _.",
            max_length=20,
        ),
        Column("titulo_curto", Kind.TEXT, True, "Nome curto que o hóspede vê, ex.: Casa da Praia."),
        Column("bairro_ou_modulo", Kind.TEXT, False, "Bairro, módulo ou condomínio."),
        Column(
            "link_fotos",
            Kind.URL,
            False,
            "Link público das fotos (começa com https://).",
            max_length=500,
        ),
        Column("capacidade_max", Kind.INT, True, "Número máximo de hóspedes.", min_value=1),
        Column("quartos", Kind.INT, False, "Número de quartos."),
        Column("suites", Kind.INT, False, "Quantos dos quartos são suítes."),
        Column("banheiros", Kind.INT, False, "Número de banheiros."),
        Column("vagas", Kind.INT, False, "Vagas de garagem."),
        _attr("piscina", "Tem piscina"),
        _attr("aceita_pet", "Aceita animais de estimação"),
        _attr("permite_festa", "Permite festas"),
        _attr("ar_quartos", "Tem ar-condicionado nos quartos"),
        _attr("wifi", "Tem wi-fi"),
        _attr("churrasqueira", "Tem churrasqueira"),
        Column("distancia_praia_m", Kind.INT, False, "Distância até a praia, em metros."),
        Column(
            "observacoes_publicas",
            Kind.TEXT,
            False,
            "Informações que podem ser passadas ao hóspede.",
            max_length=500,
        ),
    ),
)

TARIFAS: Final = Sheet(
    name="Tarifas",
    max_rows=20000,
    help=(
        "Uma linha por período de preço. Os períodos da mesma casa não podem se sobrepor. "
        "O 'fim' é o dia da saída: a noite do 'fim' não está incluída, como no check-out."
    ),
    columns=(
        Column("codigo", Kind.CODE, True, "Código da casa (aba Imoveis).", max_length=20),
        Column("inicio", Kind.DATE, True, "Primeira noite do período (dd/mm/aaaa)."),
        Column(
            "fim",
            Kind.DATE,
            True,
            "Dia da saída (dd/mm/aaaa). Ex.: 28/12 a 02/01 = noites de 28/12 a 01/01.",
        ),
        Column("diaria", Kind.MONEY, True, "Valor da diária em reais, ex.: 1500,00."),
        Column("minimo_noites", Kind.INT, False, "Mínimo de noites (vazio = 1).", min_value=1),
        Column("pacote_nome", Kind.TEXT, False, "Nome do pacote, ex.: Réveillon.", max_length=60),
        Column("pacote_valor", Kind.MONEY, False, "Valor do pacote inteiro, em reais."),
        Column(
            "pacote_fechado",
            Kind.BOOL,
            False,
            "Sim = só aluga o pacote completo. Não = aceita parte, em diárias.",
        ),
        Column("taxa_limpeza", Kind.MONEY, False, "Taxa de limpeza por estadia (vazio = 0)."),
    ),
)

BLOQUEIOS: Final = Sheet(
    name="Bloqueios",
    max_rows=20000,
    help="Uma linha por período ocupado. O 'fim' é o dia da saída, como no check-out.",
    columns=(
        Column("codigo", Kind.CODE, True, "Código da casa (aba Imoveis).", max_length=20),
        Column("inicio", Kind.DATE, True, "Dia da entrada (dd/mm/aaaa)."),
        Column("fim", Kind.DATE, True, "Dia da saída (dd/mm/aaaa)."),
    ),
)

REGRAS: Final = Sheet(
    name="Regras",
    max_rows=50,
    help="Regras curtas, aprovadas pela imobiliária, que o assistente pode repassar.",
    columns=(
        Column("chave", Kind.TEXT, True, "Assunto, ex.: check-in.", max_length=40),
        Column("valor", Kind.TEXT, True, "Texto curto, ex.: a partir das 14h.", max_length=300),
    ),
)

SHEETS: Final = (IMOVEIS, TARIFAS, BLOQUEIOS, REGRAS)
DEFAULT_RULE_KEYS: Final = ("check-in", "check-out", "caução", "pagamento", "cancelamento")

TRUE_WORDS: Final = frozenset({"sim", "s", "yes", "true", "verdadeiro", "1"})
FALSE_WORDS: Final = frozenset({"nao", "não", "n", "no", "false", "falso", "0"})


def normalize_name(value: str) -> str:
    """Compara nomes de aba e coluna sem acento, caixa ou espaços nas pontas."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).replace(" ", "_")
