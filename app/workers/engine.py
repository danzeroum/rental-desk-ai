"""Motor de uma volta da conversa (SPEC, seção 3, passos 5 a 10), sem banco.

extração (LLM, JSON com esquema) → mescla no estado (código) → decisão (código) →
busca e cálculo (código) → texto por template + duas frases do LLM validadas →
resultado. O pipeline carrega o contexto, chama o motor e cuida do envio e da
gravação. O conjunto de ouro chama o motor direto.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from app.adapters.llm import LLMClient, LLMUnavailableError, loads_object
from app.core.logging import get_logger
from app.domain import notices, render
from app.domain.catalog import BlockData, PropertyData, RateData
from app.domain.decision import Action, Decision, HandoffReason, decide
from app.domain.extraction import Extraction, parse_extraction
from app.domain.prompts import (
    EXTRACTION_SCHEMA,
    EXTRACTION_SYSTEM,
    WRITER_SCHEMA,
    WRITER_SYSTEM,
    HistoryItem,
    extraction_input,
    writer_input,
)
from app.domain.search import Attribute, SearchOutcome, SearchQuery, search
from app.domain.state import ConversationState, record_offer, stay_from
from app.domain.validator import validate_phrase

MAX_WRITER_ATTEMPTS: Final = 2
log = get_logger(__name__)


@dataclass(frozen=True)
class Catalog:
    snapshot_hash: str
    properties: Sequence[PropertyData]
    rates: Sequence[RateData]
    blocks: Sequence[BlockData]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(p.code for p in self.properties)

    def property(self, code: str) -> PropertyData | None:
        return next((p for p in self.properties if p.code == code), None)


@dataclass(frozen=True)
class TurnContext:
    today: date
    tenant_name: str
    assistant_name: str
    tone: str
    privacy_url: str
    first_response: bool
    state: ConversationState
    history: Sequence[HistoryItem]
    new_text: str | None
    catalog: Catalog | None
    data_fresh: bool
    audio_not_understood: bool = False
    brokers_back: str | None = None
    """Fora do horário dos corretores: quando voltam (ex.: "amanhã a partir das 9h")."""


@dataclass(frozen=True)
class HandoffPlan:
    reason: str
    summary: dict[str, Any]


@dataclass
class TurnResult:
    text: str
    state: ConversationState
    decision: Decision | None = None
    extraction: dict[str, Any] | None = None
    search_query: dict[str, Any] | None = None
    search: SearchOutcome | None = None
    handoff: HandoffPlan | None = None
    validator: dict[str, Any] = field(default_factory=dict)
    llm_unavailable: bool = False
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    """Uma entrada por chamada ao LLM: modelo que respondeu, fallback, tokens, latência."""

    @property
    def refusal_fallbacks(self) -> list[dict[str, Any]]:
        return [c for c in self.llm_calls if c.get("fallback_from")]


class ConversationEngine:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(self, ctx: TurnContext) -> TurnResult:
        calls: list[dict[str, Any]] = []
        result = await self._body(ctx, calls)
        result.llm_calls = calls
        if ctx.first_response:
            intro = render.intro(ctx.assistant_name, ctx.tenant_name, ctx.privacy_url)
            result.text = f"{intro}\n\n{result.text}"
        return result

    async def _body(self, ctx: TurnContext, calls: list[dict[str, Any]]) -> TurnResult:
        if ctx.audio_not_understood and not ctx.new_text:
            return TurnResult(render.AUDIO_NOT_UNDERSTOOD, ctx.state)
        try:
            extraction = await self._extract(ctx, calls)
        except LLMUnavailableError:
            plan = self._handoff_plan(ctx.state, HandoffReason.LLM_UNAVAILABLE, None)
            return TurnResult(render.CONTINGENCY, ctx.state, handoff=plan, llm_unavailable=True)
        decision = decide(extraction, ctx.state, today=ctx.today)
        result = await self._act(ctx, decision, calls)
        result.decision = decision
        result.extraction = extraction.model_dump(mode="json") if extraction else None
        return result

    async def _extract(self, ctx: TurnContext, calls: list[dict[str, Any]]) -> Extraction | None:
        prompt = extraction_input(
            today=ctx.today, state=ctx.state, history=ctx.history, new_text=ctx.new_text or ""
        )
        response = await self.llm.complete_json(
            system=EXTRACTION_SYSTEM, user=prompt, schema=EXTRACTION_SCHEMA
        )
        calls.append(response.audit("extraction"))
        parsed = loads_object(response.text)
        extraction = parse_extraction(parsed) if parsed is not None else None
        if extraction is None:
            log.warning("extracao_invalida")  # RF03: não chega à decisão
        return extraction

    async def _act(
        self, ctx: TurnContext, decision: Decision, calls: list[dict[str, Any]]
    ) -> TurnResult:
        state = decision.state
        match decision.action:
            case Action.HANDOFF:
                reason = decision.reason or HandoffReason.HUMAN
                if reason is HandoffReason.OTHER_LANGUAGE:
                    text = render.OTHER_LANGUAGE
                elif ctx.brokers_back:
                    text = notices.HANDOFF_OUT_OF_HOURS.format(when=ctx.brokers_back)
                else:
                    text = render.HANDOFF
                plan = self._handoff_plan(state, reason, decision.property_code)
                return TurnResult(text, state, handoff=plan)
            case Action.ASK:
                text = render.question_for(decision.missing or ("check_in", "check_out", "guests"))
                if decision.past_dates:
                    text = render.PAST_DATES
                return TurnResult(text, state)
            case Action.ASK_REFERENCE:
                return TurnResult(render.NEED_REFERENCE, state)
            case Action.ACKNOWLEDGE:
                return TurnResult(render.ACKNOWLEDGE, state)
            case Action.ANSWER_ATTRIBUTE:
                return self._answer_attribute(ctx, decision)
            case Action.SEARCH:
                return await self._search(ctx, state, calls)

    def _answer_attribute(self, ctx: TurnContext, decision: Decision) -> TurnResult:
        prop = ctx.catalog.property(decision.property_code or "") if ctx.catalog else None
        answer = render.answer_attribute(prop, decision.attribute) if prop else None
        if answer is not None:
            return TurnResult(answer, decision.state)
        # RF09: sem dado → "vou confirmar com o corretor" + pendência no resumo.
        pending = f"{decision.property_code}: {decision.attribute or 'item não identificado'}"
        questions = list(dict.fromkeys([*decision.state.pending_questions, pending]))[-10:]
        state = decision.state.model_copy(update={"pending_questions": questions})
        return TurnResult(render.ATTRIBUTE_UNKNOWN, state)

    async def _search(
        self, ctx: TurnContext, state: ConversationState, calls: list[dict[str, Any]]
    ) -> TurnResult:
        stay = stay_from(state)
        if ctx.catalog is None or stay is None or state.slots.guests is None:
            plan = self._handoff_plan(state, HandoffReason.HUMAN, None)
            return TurnResult(render.HANDOFF, state, handoff=plan)
        query = SearchQuery(
            stay=stay,
            guests=state.slots.guests,
            required=frozenset(Attribute(a) for a in state.slots.required),
            budget_max_cents=state.slots.budget_max_cents,
        )
        outcome = search(
            query, ctx.catalog.properties, ctx.catalog.rates, ctx.catalog.blocks,
            data_fresh=ctx.data_fresh,
        )  # fmt: skip
        query_json = {
            "check_in": stay.check_in.isoformat(),
            "check_out": stay.check_out.isoformat(),
            "guests": query.guests,
            "required": sorted(query.required),
            "budget_max_cents": query.budget_max_cents,
            "data_fresh": ctx.data_fresh,
        }
        if not outcome.offers:
            return TurnResult(render.NO_RESULTS, state, search_query=query_json, search=outcome)
        state = record_offer(state, [o.property.code for o in outcome.offers])
        cards = render.render_offers(outcome, stay.check_in, stay.check_out)
        opening, closing, validator = await self._phrases(ctx, outcome, calls)
        text = f"{opening}\n\n{cards}\n\n{closing}"
        return TurnResult(text, state, search_query=query_json, search=outcome, validator=validator)

    async def _phrases(
        self, ctx: TurnContext, outcome: SearchOutcome, calls: list[dict[str, Any]]
    ) -> tuple[str, str, dict[str, Any]]:
        """Duas frases do LLM; validador recusa → tenta mais uma vez → frase fixa (SPEC 3.10)."""
        codes = ctx.catalog.codes if ctx.catalog else frozenset()
        flags = [str(f) for o in outcome.offers for f in o.quote.flags]
        prompt = writer_input(
            tone=ctx.tone, options=len(outcome.offers), flags=flags,
            over_budget=outcome.over_budget, data_stale=outcome.data_stale,
        )  # fmt: skip
        rejections: list[list[str]] = []
        for _ in range(MAX_WRITER_ATTEMPTS):
            try:
                response = await self.llm.complete_json(
                    system=WRITER_SYSTEM, user=prompt, schema=WRITER_SCHEMA, max_tokens=300
                )
                calls.append(response.audit("writer"))
            except LLMUnavailableError:
                rejections.append(["llm_unavailable"])
                break
            data = loads_object(response.text) or {}
            opening, closing = str(data.get("abertura", "")), str(data.get("fechamento", ""))
            reasons = [
                str(r)
                for phrase in (opening, closing)
                for r in validate_phrase(phrase, property_codes=codes).reasons
            ]
            if not reasons:
                return (
                    opening.strip(),
                    closing.strip(),
                    {"attempts": len(rejections) + 1, "rejections": rejections, "fallback": False},
                )
            rejections.append(sorted(set(reasons)))
        log.info("frases_fixas_usadas", rejections=rejections)
        return (
            render.FALLBACK_OPENING,
            render.FALLBACK_CLOSING,
            {"attempts": len(rejections), "rejections": rejections, "fallback": True},
        )

    @staticmethod
    def _handoff_plan(
        state: ConversationState, reason: HandoffReason, code: str | None
    ) -> HandoffPlan:
        return HandoffPlan(
            reason=str(reason),
            summary={
                "pedido": state.slots.model_dump(mode="json"),
                "casas_oferecidas": state.last_offered,
                "casa_escolhida": code,
                "pendencias": state.pending_questions,
            },
        )
