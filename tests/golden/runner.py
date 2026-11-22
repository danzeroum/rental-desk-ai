"""Executor do conjunto de ouro (SPEC, seção 11).

Cada caso é uma conversa (uma ou mais mensagens do hóspede) contra o motor de
conversa, com a imobiliária de exemplo (Demo Riviera). Além das expectativas do
caso, toda resposta passa pelos critérios de produção do SPEC:

- zero fatos fora da base: todo valor, data, código e mínimo de noites citado no
  texto existe no resultado da busca daquela volta;
- 100% das respostas com cards têm o aviso legal;
- nenhuma afirmação de reserva confirmada;
- passagem ao corretor correta (taxa ≥ 95% com o LLM real; 100% com o FakeLLM).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

from app.adapters.fake_llm import FakeLLM
from app.adapters.llm import LLMClient, LLMResponse
from app.domain.catalog import BlockData
from app.domain.masking import mask_pii
from app.domain.prompts import HistoryItem
from app.domain.render import LEGAL_NOTICE, br_date, brl
from app.domain.state import ConversationState
from app.importer.sample import DEMO_TENANT_NAME, build_sample
from app.workers.engine import Catalog, ConversationEngine, TurnContext, TurnResult

CASES_DIR: Final = Path(__file__).parent / "cases"
_MONEY: Final = re.compile(r"R\$ [\d.]+,\d{2}")
_DATE: Final = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_CODE: Final = re.compile(r"\b[A-Z]{3,}\d{1,3}\b")
_MIN_NIGHTS: Final = re.compile(r"Mínimo de (\d+) noites")
_RESERVATION_CLAIMS: Final = re.compile(
    r"\b(reserva (está |foi )?confirmada|está reservad[ao]|confirmad[ao]s?|garantid[ao]s?)\b",
    re.IGNORECASE,
)


@dataclass
class Turn:
    guest: str
    expect: dict[str, Any] = field(default_factory=dict)
    audio_failed: bool = False


@dataclass
class Case:
    id: str
    kind: str
    description: str
    turns: list[Turn]
    today: date = date(2026, 11, 21)
    data_fresh: bool = True
    llm: str = "ok"  # ok | down | bad_writer
    brokers_back: str | None = None  # fora do horário: quando os corretores voltam
    extra_blocks: list[BlockData] = field(default_factory=list)


@dataclass
class TurnReport:
    case: str
    turn: int
    text: str
    failures: list[str]
    fact_violations: list[str]
    has_cards: bool
    has_notice: bool
    reservation_claim: bool
    handoff_expected: str | None
    handoff_actual: str | None
    llm_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    models: tuple[str, ...] = ()
    refusal_fallbacks: int = 0


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")):
            cases.append(
                Case(
                    id=raw["id"],
                    kind=raw["kind"],
                    description=raw["description"],
                    turns=[Turn(**t) for t in raw["turns"]],
                    today=raw.get("today", date(2026, 11, 21)),
                    data_fresh=raw.get("data_fresh", True),
                    llm=raw.get("llm", "ok"),
                    brokers_back=raw.get("brokers_back"),
                    extra_blocks=[
                        BlockData(b["codigo"], b["inicio"], b["fim"]) for b in raw.get("blocks", [])
                    ],
                )
            )
    return cases


def demo_catalog(case: Case) -> Catalog:
    sample = build_sample(2026)
    return Catalog(
        snapshot_hash=sample.content_hash(),
        properties=sample.properties,
        rates=sample.rates,
        blocks=[*sample.blocks, *case.extra_blocks],
    )


class RecordingLLM:
    """Guarda os prompts enviados (para conferir que dados pessoais não saem)."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.prompts: list[str] = []

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int = 1024
    ) -> LLMResponse:
        self.prompts.append(user)
        return await self.inner.complete_json(
            system=system, user=user, schema=schema, max_tokens=max_tokens
        )


def fake_for(case: Case) -> LLMClient:
    if case.llm == "down":
        return FakeLLM(fail=True)
    if case.llm == "bad_writer":
        return FakeLLM(
            writer_text=(
                "A CASA01 sai por 1500 reais, confirmado!",
                "Garanto vinte por cento de desconto.",
            )
        )
    return FakeLLM()


def fact_violations(result: TurnResult) -> list[str]:
    """Fatos no texto que não vêm do resultado da busca desta volta."""
    text = result.text
    allowed_money: set[str] = set()
    allowed_dates: set[str] = set()
    allowed_codes: set[str] = set()
    allowed_min: set[str] = set()
    if result.search_query:
        allowed_dates |= {
            br_date(date.fromisoformat(result.search_query[k])) for k in ("check_in", "check_out")
        }
    for offer in result.search.offers if result.search else ():
        q = offer.quote
        allowed_money.add(brl(q.total_cents))
        allowed_codes.add(offer.property.code)
        if q.min_nights_required:
            allowed_min.add(str(q.min_nights_required))
        if q.package:
            allowed_money.add(brl(q.package.total_cents))
            allowed_dates |= {br_date(q.package.start_date), br_date(q.package.end_date)}
    if result.decision and result.decision.property_code:
        allowed_codes.add(result.decision.property_code)
    violations = [f"valor {m}" for m in _MONEY.findall(text) if m not in allowed_money]
    violations += [f"data {d}" for d in _DATE.findall(text) if d not in allowed_dates]
    violations += [f"código {c}" for c in _CODE.findall(text) if c not in allowed_codes]
    violations += [f"mínimo {n}" for n in _MIN_NIGHTS.findall(text) if n not in allowed_min]
    return violations


def _check_expect(expect: dict[str, Any], result: TurnResult) -> list[str]:
    failures: list[str] = []
    action = str(result.decision.action) if result.decision else None
    if result.llm_unavailable:
        action = "handoff"
    if "action" in expect and action != expect["action"]:
        failures.append(f"ação {action}, esperada {expect['action']}")
    if "action_in" in expect and action not in expect["action_in"]:
        failures.append(f"ação {action}, esperada uma de {expect['action_in']}")
    actual_handoff = result.handoff.reason if result.handoff else None
    if "handoff" in expect and actual_handoff != expect["handoff"]:
        failures.append(f"passagem {actual_handoff}, esperada {expect['handoff']}")
    offers = [o.property.code for o in result.search.offers] if result.search else []
    if "offers" in expect and offers != expect["offers"]:
        failures.append(f"casas {offers}, esperadas {expect['offers']}")
    for code in expect.get("offers_exclude", []):
        if code in offers:
            failures.append(f"casa {code} não deveria ser oferecida")
    for snippet in expect.get("contains", []):
        if snippet not in result.text:
            failures.append(f"faltou: {snippet!r}")
    for snippet in expect.get("not_contains", []):
        if snippet.lower() in result.text.lower():
            failures.append(f"não deveria conter: {snippet!r}")
    if "chosen" in expect:
        chosen = result.handoff.summary.get("casa_escolhida") if result.handoff else None
        if chosen != expect["chosen"]:
            failures.append(f"casa escolhida {chosen}, esperada {expect['chosen']}")
    return failures


async def run_case(case: Case, llm: LLMClient | None = None) -> list[TurnReport]:
    recorder = RecordingLLM(llm or fake_for(case))
    engine = ConversationEngine(recorder)
    catalog = demo_catalog(case)
    state = ConversationState()
    history: list[HistoryItem] = []
    reports: list[TurnReport] = []
    for index, turn in enumerate(case.turns):
        ctx = TurnContext(
            today=case.today, tenant_name=DEMO_TENANT_NAME, assistant_name="Lia", tone="formal",
            privacy_url="https://app.exemplo.com.br/privacidade/demo-riviera/v1",
            first_response=index == 0, state=state, history=list(history),
            new_text=None if turn.audio_failed else turn.guest, catalog=catalog,
            data_fresh=case.data_fresh, audio_not_understood=turn.audio_failed,
            brokers_back=case.brokers_back,
        )  # fmt: skip
        result = await engine.run(ctx)
        failures = _check_expect(turn.expect, result)
        masked = mask_pii(turn.guest)
        if masked != turn.guest:
            leaked = [p for p in recorder.prompts if turn.guest in p]
            if leaked:
                failures.append("dado pessoal enviado ao LLM sem máscara")
        has_cards = bool(result.search and result.search.offers)
        reports.append(
            TurnReport(
                case=case.id, turn=index + 1, text=result.text, failures=failures,
                fact_violations=fact_violations(result), has_cards=has_cards,
                has_notice=LEGAL_NOTICE in result.text,
                reservation_claim=bool(_RESERVATION_CLAIMS.search(result.text)),
                handoff_expected=turn.expect.get("handoff"),
                handoff_actual=result.handoff.reason if result.handoff else None,
                llm_seconds=sum(c["latency_ms"] for c in result.llm_calls) / 1000,
                input_tokens=sum(c["input_tokens"] for c in result.llm_calls),
                output_tokens=sum(c["output_tokens"] for c in result.llm_calls),
                models=tuple(c["model"] for c in result.llm_calls),
                refusal_fallbacks=len(result.refusal_fallbacks),
            )
        )  # fmt: skip
        state = result.state
        history += [HistoryItem(True, turn.guest), HistoryItem(False, result.text)]
    return reports


@dataclass(frozen=True)
class Metrics:
    turns: int
    fact_violations: int
    cards: int
    cards_with_notice: int
    reservation_claims: int
    handoff_cases: int
    handoff_correct: int
    expectation_failures: int

    @property
    def handoff_accuracy(self) -> float:
        return self.handoff_correct / self.handoff_cases if self.handoff_cases else 1.0


def metrics(reports: list[TurnReport]) -> Metrics:
    handoff_turns = [r for r in reports if r.handoff_expected is not None]
    return Metrics(
        turns=len(reports),
        fact_violations=sum(len(r.fact_violations) for r in reports),
        cards=sum(r.has_cards for r in reports),
        cards_with_notice=sum(r.has_cards and r.has_notice for r in reports),
        reservation_claims=sum(r.reservation_claim for r in reports),
        handoff_cases=len(handoff_turns),
        handoff_correct=sum(r.handoff_actual == r.handoff_expected for r in handoff_turns),
        expectation_failures=sum(len(r.failures) for r in reports),
    )
