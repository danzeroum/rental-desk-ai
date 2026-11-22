"""Textos montados pelo código (Jinja): cards, perguntas, respostas e avisos (RF08).

Valores, datas, códigos e o aviso legal só existem aqui; o LLM nunca os escreve.
Puro: os templates vivem neste módulo, sem leitura de arquivo.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from jinja2 import Environment, StrictUndefined, select_autoescape

from app.domain.catalog import PropertyData
from app.domain.pricing import Flag
from app.domain.search import Offer, SearchOutcome

LEGAL_NOTICE: Final = "Valores estimados e disponibilidade sujeitos à confirmação do corretor."

_env = Environment(
    # Templates em texto puro de WhatsApp: escape de HTML só para arquivos .html/.xml.
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def brl(cents: int) -> str:
    """12345678 → "R$ 123.456,78"."""
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def br_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


_env.filters.update(brl=brl, br_date=br_date)

_CARD = _env.from_string(
    """*{{ p.title }}* ({{ p.code }})
Até {{ p.max_guests }} pessoas{% if p.neighborhood %} · {{ p.neighborhood }}{% endif %}

{% if pkg and full_package %}
Esta casa só aluga o pacote {{ pkg.name }} completo: {{ pkg.start_date | br_date }} a {{ pkg.end_date | br_date }}, {{ pkg.total_cents | brl }}.
Estimativa com o pacote completo: {{ q.total_cents | brl }}
{% elif pkg %}
Estimativa: {{ q.total_cents | brl }} ({{ nights }}, {{ check_in | br_date }} a {{ check_out | br_date }}), com o pacote {{ pkg.name }}
{% else %}
Estimativa: {{ q.total_cents | brl }} ({{ nights }}, {{ check_in | br_date }} a {{ check_out | br_date }})
{% endif %}
{% if min_nights %}
Mínimo de {{ min_nights }} noites neste período.
{% endif %}
{% if over_budget %}
Acima do orçamento informado.
{% endif %}
{% if not confirmed %}
Disponibilidade a confirmar com o corretor.
{% endif %}
{% if p.photos_url %}
Fotos: {{ p.photos_url }}
{% endif %}"""
)


def render_card(offer: Offer, check_in: date, check_out: date) -> str:
    q = offer.quote
    return _CARD.render(
        p=offer.property,
        q=q,
        pkg=q.package,
        full_package=Flag.REQUIRES_FULL_PACKAGE in q.flags,
        min_nights=q.min_nights_required if Flag.BELOW_MIN_NIGHTS in q.flags else None,
        nights=plural(q.nights, "noite", "noites"),
        check_in=check_in,
        check_out=check_out,
        over_budget=offer.over_budget,
        confirmed=offer.availability_confirmed,
    ).strip()


def render_offers(outcome: SearchOutcome, check_in: date, check_out: date) -> str:
    """Cards numerados (o hóspede pode dizer "a segunda") + aviso legal, sempre."""
    blocks = [
        f"{index}) {render_card(offer, check_in, check_out)}"
        for index, offer in enumerate(outcome.offers, start=1)
    ]
    header = []
    if outcome.over_budget:
        header.append("Não encontrei casas dentro do orçamento; estas são as mais próximas:")
    if outcome.data_stale:
        header.append("O calendário das casas ainda não foi atualizado hoje pela imobiliária.")
    return "\n\n".join([*header, *blocks, LEGAL_NOTICE])


# --- Perguntas e respostas fixas -------------------------------------------------

QUESTIONS: Final = {
    "dates": "Para quais datas? Me diga o dia de entrada e o de saída (ex.: 28/12 a 02/01).",
    "check_in": "Qual é o dia de entrada?",
    "check_out": "Qual é o dia de saída?",
    "guests": "Para quantas pessoas, contando as crianças?",
}
PAST_DATES: Final = "Essa data já passou. Pode me dizer as datas de entrada e de saída?"
NO_RESULTS: Final = (
    "Não encontrei casas na nossa base para esse pedido. Se quiser, mude as datas, o número de "
    "pessoas ou os itens que precisa, ou peça para falar com um corretor."
)
ATTRIBUTE_UNKNOWN: Final = "Essa informação eu vou confirmar com o corretor."
NEED_REFERENCE: Final = (
    "De qual casa você está falando? Pode me dizer o número da opção ou o código."
)
HANDOFF: Final = (
    "Vou passar sua conversa para um corretor, que continua o atendimento por aqui. "
    "Já deixei anotado o que você pediu."
)
OTHER_LANGUAGE: Final = (
    "Olá! Sou um assistente virtual e, por enquanto, atendo só em português. Um corretor vai "
    "continuar a conversa com você.\n\n"
    "Hello! I am a virtual assistant and I can only reply in Portuguese for now. "
    "A broker will continue this conversation with you."
)
CONTINGENCY: Final = "Recebemos sua mensagem; um corretor responde em breve."
"""SPEC, seção 7, item 10: provedor de IA fora do ar."""
AUDIO_NOT_UNDERSTOOD: Final = "Não consegui ouvir o seu áudio. Pode escrever, por favor?"
FALLBACK_OPENING: Final = "Separei estas opções para você:"
FALLBACK_CLOSING: Final = "Se alguma interessar, me diga qual que eu chamo um corretor."
ACKNOWLEDGE: Final = (
    "Fico à disposição! Se alguma casa interessar, me diga qual. Se quiser outras opções, "
    "é só mudar as datas, o número de pessoas ou o que precisa."
)


def question_for(missing: Sequence[str]) -> str:
    if "check_in" in missing and "check_out" in missing:
        first = QUESTIONS["dates"]
    else:
        first = QUESTIONS[missing[0]]
    if "guests" in missing and first != QUESTIONS["guests"]:
        return f"{first} E para quantas pessoas?"
    return first


def intro(assistant_name: str, tenant_name: str, privacy_url: str) -> str:
    """RF02: toda primeira resposta se identifica como assistente virtual e traz o aviso."""
    name = assistant_name.strip()
    who = (
        f"o assistente virtual da {tenant_name}"
        if not name or _fold(name).startswith("assistente")
        else f"{name}, assistente virtual da {tenant_name}"
    )
    return f"Olá! Sou {who}. Aviso de privacidade: {privacy_url}"


# --- Atributos da casa -------------------------------------------------------------


@dataclass(frozen=True)
class AttributeSpec:
    field: str
    label: str
    kind: str  # "bool" | "count" | "meters" | "text"


ATTRIBUTES: Final[dict[str, AttributeSpec]] = {
    "piscina": AttributeSpec("has_pool", "piscina", "bool"),
    "pet": AttributeSpec("accepts_pets", "aceita animais de estimação", "bool"),
    "animal": AttributeSpec("accepts_pets", "aceita animais de estimação", "bool"),
    "cachorro": AttributeSpec("accepts_pets", "aceita animais de estimação", "bool"),
    "festa": AttributeSpec("allows_parties", "permite festas", "bool"),
    "ar_quartos": AttributeSpec("ac_in_bedrooms", "ar-condicionado nos quartos", "bool"),
    "ar condicionado": AttributeSpec("ac_in_bedrooms", "ar-condicionado nos quartos", "bool"),
    "ar-condicionado": AttributeSpec("ac_in_bedrooms", "ar-condicionado nos quartos", "bool"),
    "wifi": AttributeSpec("has_wifi", "wi-fi", "bool"),
    "wi-fi": AttributeSpec("has_wifi", "wi-fi", "bool"),
    "internet": AttributeSpec("has_wifi", "wi-fi", "bool"),
    "churrasqueira": AttributeSpec("has_barbecue", "churrasqueira", "bool"),
    "quartos": AttributeSpec("bedrooms", "quartos", "count"),
    "suites": AttributeSpec("suites", "suítes", "count"),
    "banheiros": AttributeSpec("bathrooms", "banheiros", "count"),
    "vagas": AttributeSpec("parking_spots", "vagas de garagem", "count"),
    "garagem": AttributeSpec("parking_spots", "vagas de garagem", "count"),
    "praia": AttributeSpec("beach_distance_m", "distância até a praia", "meters"),
    "distancia da praia": AttributeSpec("beach_distance_m", "distância até a praia", "meters"),
    "capacidade": AttributeSpec("max_guests", "capacidade", "count"),
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.strip().lower().replace("_", " "))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_FOLDED: Final = {_fold(key): spec for key, spec in ATTRIBUTES.items()}


def attribute_spec(asked: str | None) -> AttributeSpec | None:
    """Só casa nomes exatos: "piscina aquecida" NÃO é "piscina" (RF09: vira pendência)."""
    return _FOLDED.get(_fold(asked)) if asked else None


def answer_attribute(prop: PropertyData, asked: str | None) -> str | None:
    """Resposta montada só com o dado da planilha; `None` quando o dado não existe (RF09)."""
    spec = attribute_spec(asked)
    if spec is None:
        return None
    value = getattr(prop, spec.field)
    if value is None:
        return None
    subject = f"{prop.title} ({prop.code})"  # sem artigo: o título pode ser "Sobrado", "Chalé"…
    match spec.kind:
        case "bool":
            return f"{subject}: {spec.label}, {'sim' if value else 'não'}."
        case "count":
            return (
                f"{subject} tem {value} {spec.label}."
                if spec.field != "max_guests"
                else (f"{subject} recebe até {value} pessoas.")
            )
        case "meters":
            return f"{subject} fica a {value} metros da praia."
    return None  # pragma: no cover
