"""LLM determinístico por regras, para o conjunto de ouro (`make golden`) e desenvolvimento.

Não é um substituto do modelo real: entende só os padrões usados nos casos de
teste (datas dd/mm, "para N pessoas", Réveillon, atributos, intenções óbvias).
O conjunto de ouro também roda contra o LLM real (`make golden-live`).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Any, Final

from app.adapters.llm import LLMResponse, LLMUnavailableError
from app.domain.prompts import EXTRACTION_SCHEMA, WRITER_SCHEMA

_TODAY: Final = re.compile(r"^Hoje: (\d{4}-\d{2}-\d{2})", re.MULTILINE)
_LAST: Final = re.compile(r"^hóspede \(última\): (.*)$", re.MULTILINE)
_DATE: Final = r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
_RANGE: Final = re.compile(rf"{_DATE}\s*(?:a|até|ate|-|e)\s*{_DATE}")
_SINGLE_IN: Final = re.compile(rf"(?:chego|chegando|entrada|entrar)\D{{0,12}}{_DATE}")
_SINGLE_OUT: Final = re.compile(
    rf"(?:saio|saindo|saida|sair|ate|até o dia|até)\s*(?:dia\s*)?{_DATE}"
)
_GUESTS: Final = re.compile(
    r"(?:para|pra|somos|seremos|formos|grupo de|com)\s+(\d{1,3})\s*(?:pessoas|adultos|hospedes)?"
    r"|(\d{1,3})\s+(?:pessoas|adultos|hospedes)"
)
_BUDGET: Final = re.compile(
    r"(?:ate|orcamento de|no maximo|max)\s*r?\$?\s*(\d+(?:[.,]\d{3})*)\s*(mil)?"
)
_ORDINALS: Final = {
    "primeira": "1",
    "segunda": "2",
    "terceira": "3",
    "1a": "1",
    "2a": "2",
    "3a": "3",
}
_CODE: Final = re.compile(r"\b([a-z]{3,}\d{1,3})\b")

_INTENT_WORDS: Final = (
    ("other_language", ("hello", "please", "house for", "available", "rent ", "booking")),
    ("human", ("corretor", "atendente", "humano", "pessoa de verdade", "falar com alguem")),
    ("negotiate", ("desconto", "mais barato", "abaixar", "abaixa o", "negociar", "faz por", "combinado com o dono")),
    ("payment", ("pix", "pagar", "pagamento", "boleto", "sinal", "cartao")),
    ("cancel", ("cancelar", "cancelamento", "desistir")),
    ("complaint", ("reclamacao", "reclamar", "pessimo", "absurdo", "horrivel")),
    ("out_of_scope", ("emprego", "vaga de trabalho", "comprar um imovel", "vender minha casa", "receita de", "previsao do tempo")),
    ("choose", ("quero a ", "fico com", "vou querer", "gostei da", "pode reservar", "quero essa", "vamos de")),
)  # fmt: skip
_ATTRIBUTES: Final = {
    "piscina": "piscina", "pet": "pet", "cachorro": "pet", "cachorra": "pet", "gato": "pet",
    "churrasqueira": "churrasqueira", "wifi": "wifi", "wi-fi": "wifi", "internet": "wifi",
    "ar condicionado": "ar_quartos", "ar-condicionado": "ar_quartos", "festa": "festa",
}  # fmt: skip
_GREETINGS: Final = ("oi", "ola", "bom dia", "boa tarde", "boa noite", "obrigad", "valeu")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _next_date(day: int, month: int, year: str | None, today: date) -> date | None:
    try:
        if year:
            full = int(year) + (2000 if len(year) == 2 else 0)
            return date(full, month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


class FakeLLM:
    def __init__(self, *, fail: bool = False, writer_text: tuple[str, str] | None = None) -> None:
        self.fail = fail
        self.writer_text = writer_text or (
            "Separei algumas opções que combinam com o seu pedido.",
            "Se alguma chamar sua atenção, me diga qual que eu chamo um corretor.",
        )
        self.calls: list[str] = []

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024
    ) -> LLMResponse:
        if self.fail:
            raise LLMUnavailableError("falha simulada")
        if schema is WRITER_SCHEMA:
            self.calls.append("writer")
            opening, closing = self.writer_text
            return self._response(
                json.dumps({"abertura": opening, "fechamento": closing}, ensure_ascii=False), user
            )
        if schema is not EXTRACTION_SCHEMA:
            raise LLMUnavailableError("esquema desconhecido pelo FakeLLM")
        self.calls.append("extraction")
        return self._response(json.dumps(self._extract(user), ensure_ascii=False), user)

    @staticmethod
    def _response(text: str, prompt: str) -> LLMResponse:
        # Estimativa grosseira (~4 caracteres por token), só para exercitar os relatórios.
        return LLMResponse(
            text=text,
            model="fake",
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
        )

    def _extract(self, user: str) -> dict[str, Any]:
        today_match = _TODAY.search(user)
        today = date.fromisoformat(today_match.group(1)) if today_match else date.today()  # noqa: DTZ011
        last = _LAST.search(user)
        text = _fold(last.group(1) if last else "")
        slots = self._slots(text, today)
        intent = self._intent(text, slots)
        refers_to = self._reference(text)
        attribute = (
            next((v for k, v in _ATTRIBUTES.items() if k in text), None) if "?" in text else None
        )
        if intent == "search" and attribute and refers_to is not None:
            intent = "ask_attribute"
        if intent == "search" and "tem " in text and "?" in text and not any(slots.values()):
            intent = "ask_attribute"
            attribute = attribute or text.split("tem ", 1)[1].rstrip("?! .")
        return {
            "intent": intent,
            "slots_update": slots,
            "refers_to": refers_to,
            "attribute_asked": attribute if intent == "ask_attribute" else None,
        }

    @staticmethod
    def _intent(text: str, slots: dict[str, Any]) -> str:
        for intent, words in _INTENT_WORDS:
            if any(word in f" {text} " for word in words):
                return intent
        has_slots = any(v for v in slots.values())
        if (
            not has_slots
            and any(text.strip(" !.?").startswith(g) for g in _GREETINGS)
            and len(text) < 40
        ):
            return "smalltalk"
        return "search"

    @staticmethod
    def _reference(text: str) -> str | None:
        for word, ordinal in _ORDINALS.items():
            if re.search(rf"\b{word}\b", text):
                return ordinal
        match = _CODE.search(text)
        return match.group(1).upper() if match else None

    @staticmethod
    def _slots(text: str, today: date) -> dict[str, Any]:
        check_in = check_out = None
        if match := _RANGE.search(text):
            d1, m1, y1, d2, m2, y2 = match.groups()
            check_in = _next_date(int(d1), int(m1), y1, today)
            if check_in is not None:
                check_out = _next_date(int(d2), int(m2), y2, check_in)
        elif "reveillon" in text or "ano novo" in text:
            check_in = _next_date(28, 12, None, today)
            check_out = date(check_in.year + 1, 1, 2) if check_in else None
        else:
            if match := _SINGLE_IN.search(text):
                check_in = _next_date(
                    int(match.group(1)), int(match.group(2)), match.group(3), today
                )
            if match := _SINGLE_OUT.search(text):
                check_out = _next_date(
                    int(match.group(1)), int(match.group(2)), match.group(3), today
                )
        guests = None
        if match := _GUESTS.search(text):
            guests = int(match.group(1) or match.group(2))
        budget = None
        if match := _BUDGET.search(text):
            number = int(match.group(1).replace(".", "").replace(",", ""))
            budget = number * 1000 if match.group(2) else number
        required = list(
            dict.fromkeys(v for k, v in _ATTRIBUTES.items() if k in text and "?" not in text)
        )
        return {
            "check_in": check_in.isoformat() if check_in else None,
            "check_out": check_out.isoformat() if check_out else None,
            "guests": guests,
            "budget_max_brl": budget,
            "neighborhood": None,
            "required": required,
        }
