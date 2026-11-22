"""Conjunto de ouro: FakeLLM determinístico (`make golden`) ou LLM real (`make golden-live`).

Com o FakeLLM, todo caso precisa passar por inteiro. Com o LLM real
(`GOLDEN_LIVE=1`), valem os critérios de produção do SPEC (seção 11): zero fatos
fora da base, 100% dos cards com aviso, nenhuma afirmação de reserva e passagem
correta em pelo menos 95% dos casos.
"""

from __future__ import annotations

import os

import pytest

from app.adapters.llm import AnthropicLLMClient, LLMClient
from tests.golden.runner import TurnReport, load_cases, metrics, run_case

LIVE = os.environ.get("GOLDEN_LIVE") == "1"
CASES = load_cases()


def _live_llm() -> LLMClient:
    model = os.environ.get("LLM_MODEL")
    if not model or not os.environ.get("LLM_API_KEY"):
        pytest.skip("GOLDEN_LIVE=1 exige LLM_MODEL e LLM_API_KEY")
    return AnthropicLLMClient(model=model, api_key=os.environ["LLM_API_KEY"])


@pytest.mark.skipif(LIVE, reason="no modo real valem só os critérios agregados")
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_case_with_fake_llm(case: object) -> None:
    reports = await run_case(case)  # type: ignore[arg-type]
    problems = [f"volta {r.turn}: {f}" for r in reports for f in [*r.failures, *r.fact_violations]]
    problems += [
        f"volta {r.turn}: cards sem aviso" for r in reports if r.has_cards and not r.has_notice
    ]
    problems += [f"volta {r.turn}: afirma reserva" for r in reports if r.reservation_claim]
    assert problems == [], "\n".join(problems) + "\n\n" + reports[-1].text


async def test_production_criteria() -> None:
    llm = _live_llm() if LIVE else None
    reports: list[TurnReport] = []
    for case in CASES:
        reports += await run_case(case, llm if case.llm == "ok" else None)
    m = metrics(reports)
    print(  # noqa: T201 - relatório do conjunto de ouro
        f"\nConjunto de ouro ({'LLM real' if LIVE else 'FakeLLM'}): "
        f"{len(CASES)} casos, {m.turns} voltas"
        f"\n  fatos fora da base: {m.fact_violations}"
        f"\n  cards com aviso: {m.cards_with_notice}/{m.cards}"
        f"\n  afirmações de reserva: {m.reservation_claims}"
        f"\n  passagem correta: {m.handoff_correct}/{m.handoff_cases} ({m.handoff_accuracy:.0%})"
        f"\n  expectativas não atendidas: {m.expectation_failures}"
    )
    assert m.fact_violations == 0
    assert m.cards_with_notice == m.cards
    assert m.reservation_claims == 0
    assert m.handoff_accuracy >= 0.95
    assert len(CASES) >= 100  # meta da v1.0.0: 100 (65 fluxos + 35 adversários)
