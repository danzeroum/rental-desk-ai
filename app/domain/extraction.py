"""Esquema da saída do LLM na extração (SPEC, seção 6, "Esquema da extração").

Validação estrita (RF03): qualquer JSON fora do esquema é recusado e nunca chega
à decisão. Valores impossíveis (data inexistente, hóspedes ≤ 0) também são
recusados aqui, campo a campo, sem derrubar o resto da extração.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.search import Attribute

SCHEMA_VERSION: Final = 1
MAX_GUESTS: Final = 100
MAX_BUDGET_BRL: Final = 10_000_000
_ORDINAL: Final = re.compile(r"^[1-3]$")
_CODE: Final = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")


class IntentKind(StrEnum):
    SEARCH = "search"
    ASK_ATTRIBUTE = "ask_attribute"
    CHOOSE = "choose"
    NEGOTIATE = "negotiate"
    COMPLAINT = "complaint"
    PAYMENT = "payment"
    CANCEL = "cancel"
    HUMAN = "human"
    SMALLTALK = "smalltalk"
    OUT_OF_SCOPE = "out_of_scope"
    OTHER_LANGUAGE = "other_language"


class SlotsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_in: date | None = None
    check_out: date | None = None
    guests: Annotated[int, Field(ge=1, le=MAX_GUESTS)] | None = None
    budget_max_brl: Annotated[int, Field(ge=1, le=MAX_BUDGET_BRL)] | None = None
    neighborhood: Annotated[str, Field(max_length=120)] | None = None
    required: list[Attribute] = Field(default_factory=list, max_length=len(Attribute))

    @field_validator("check_in", "check_out", "guests", "budget_max_brl", mode="wrap")
    @classmethod
    def _drop_impossible(cls, value: Any, handler: Any) -> Any:
        """Um campo impossível vira "não informado" (e depois pergunta), sem anular o resto."""
        try:
            return handler(value)
        except ValidationError:
            return None

    @field_validator("required", mode="before")
    @classmethod
    def _known_attributes_only(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        known = {a.value for a in Attribute}
        return [item for item in value if isinstance(item, str) and item in known]


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: IntentKind
    slots_update: SlotsUpdate = Field(default_factory=SlotsUpdate)
    refers_to: Annotated[str, Field(max_length=20)] | None = None
    """Código da casa (ex.: CASA12) ou ordinal "1"-"3" entre as últimas oferecidas."""
    attribute_asked: Annotated[str, Field(max_length=60)] | None = None

    @field_validator("refers_to")
    @classmethod
    def _code_or_ordinal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().upper()
        return text if _ORDINAL.match(text) or _CODE.match(text) else None


def parse_extraction(raw: str | bytes | dict[str, Any]) -> Extraction | None:
    """Valida a saída do LLM; `None` se não for um JSON dentro do esquema (RF03)."""
    try:
        if isinstance(raw, dict):
            return Extraction.model_validate(raw)
        return Extraction.model_validate_json(raw)
    except ValidationError:
        return None
