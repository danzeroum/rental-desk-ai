"""Comparação de modelos no conjunto de ouro (`make golden-live`).

Roda os casos contra cada modelo, mede acertos, critérios de produção do SPEC
(seção 11), latência p95 por volta e custo estimado por conversa, e recomenda o
modelo **mais barato que passa em todos os critérios dentro do p95 do SPEC**.

    LLM_API_KEY=... python -m tests.golden.compare
    python -m tests.golden.compare --models claude-haiku-5-5,claude-sonnet-5-5 --out x.md
    python -m tests.golden.compare --fake      # ensaio sem rede (CI)

A latência medida é a do LLM somada por volta da conversa (extração + frases), que é
quase todo o tempo de resposta depois do debounce; banco e envio somam milissegundos.
Os casos que simulam falha da IA ou frases ruins (`llm: down | bad_writer`) testam o
código, não o modelo, e rodam sempre com o FakeLLM.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from app.adapters.fake_llm import FakeLLM
from app.adapters.llm import AnthropicLLMClient, LLMClient
from tests.golden.runner import Case, TurnReport, load_cases, metrics, run_case

DEFAULT_MODELS: Final = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
SPEC_P95_SECONDS: Final = 15.0
HANDOFF_MIN: Final = 0.95

# US$ por milhão de tokens (entrada, saída), tabela pública de nov/2026. Haiku 5.5: preço
# para prompts de até 100 mil tokens (os nossos têm poucos milhares). Atualize se mudar.
PRICES_USD_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}


@dataclass(frozen=True)
class ModelResult:
    model: str
    cases: int
    turns: int
    turns_ok: int
    fact_violations: int
    cards: int
    cards_with_notice: int
    reservation_claims: int
    handoff_accuracy: float
    p95_seconds: float
    cost_per_conversation_usd: float
    refusal_fallbacks: int

    @property
    def accuracy(self) -> float:
        return self.turns_ok / self.turns if self.turns else 0.0

    @property
    def failed_criteria(self) -> list[str]:
        failed = []
        if self.fact_violations:
            failed.append("fatos fora da base")
        if self.cards_with_notice < self.cards:
            failed.append("cards sem aviso")
        if self.reservation_claims:
            failed.append("afirma reserva")
        if self.handoff_accuracy < HANDOFF_MIN:
            failed.append("passagem < 95%")
        if self.p95_seconds > SPEC_P95_SECONDS:
            failed.append(f"p95 > {SPEC_P95_SECONDS:.0f} s")
        return failed

    @property
    def approved(self) -> bool:
        return not self.failed_criteria


def percentile(values: Sequence[float], pct: float) -> float:
    """Percentil pelo método do posto mais próximo (sem interpolação)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def turn_cost_usd(report: TurnReport, model: str) -> float:
    price_in, price_out = PRICES_USD_PER_MTOK.get(model, (0.0, 0.0))
    return (report.input_tokens * price_in + report.output_tokens * price_out) / 1_000_000


def summarize(model: str, cases: Sequence[Case], reports: Sequence[TurnReport]) -> ModelResult:
    m = metrics(list(reports))
    live_cases = {c.id for c in cases if c.llm == "ok"}
    live = [r for r in reports if r.case in live_cases]
    cost = sum(turn_cost_usd(r, model) for r in live) / max(len(live_cases), 1)
    return ModelResult(
        model=model,
        cases=len(cases),
        turns=m.turns,
        turns_ok=sum(not r.failures and not r.fact_violations for r in reports),
        fact_violations=m.fact_violations,
        cards=m.cards,
        cards_with_notice=m.cards_with_notice,
        reservation_claims=m.reservation_claims,
        handoff_accuracy=m.handoff_accuracy,
        p95_seconds=percentile([r.llm_seconds for r in live], 95),
        cost_per_conversation_usd=cost,
        refusal_fallbacks=sum(r.refusal_fallbacks for r in reports),
    )


def choose(results: Sequence[ModelResult]) -> ModelResult | None:
    """O mais barato entre os aprovados em todos os critérios (SPEC + p95)."""
    approved = [r for r in results if r.approved]
    return min(approved, key=lambda r: r.cost_per_conversation_usd) if approved else None


def render_table(results: Sequence[ModelResult], *, fake: bool) -> str:
    lines = [
        f"# Conjunto de ouro: comparação de modelos ({date.today():%d/%m/%Y})",  # noqa: DTZ011
        "",
        "Ensaio com FakeLLM (sem rede): valores de latência e custo não representam modelos reais."
        if fake
        else f"Critérios: SPEC seção 11 e p95 ≤ {SPEC_P95_SECONDS:.0f} s por volta (LLM).",
        "",
        "| Modelo | Acertos | Fatos fora da base | Cards com aviso | Afirma reserva | "
        "Passagem correta | p95 (s) | Custo/conversa (US$) | Fallback de recusa | Resultado |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "✅ aprovado" if r.approved else "❌ " + ", ".join(r.failed_criteria)
        lines.append(
            f"| `{r.model}` | {r.turns_ok}/{r.turns} ({r.accuracy:.0%}) | {r.fact_violations} | "
            f"{r.cards_with_notice}/{r.cards} | {r.reservation_claims} | "
            f"{r.handoff_accuracy:.0%} | {r.p95_seconds:.1f} | {r.cost_per_conversation_usd:.4f} | "
            f"{r.refusal_fallbacks} | {verdict} |"
        )
    chosen = choose(results)
    lines += [
        "",
        f"**Recomendação:** `{chosen.model}` (o mais barato aprovado em todos os critérios)."
        if chosen
        else "**Recomendação:** nenhum modelo passou em todos os critérios; não trocar o padrão.",
        "",
        f"{results[0].cases if results else 0} casos por modelo. Custo estimado pelos tokens "
        "medidos e pela tabela de preços em `tests/golden/compare.py`.",
    ]
    return "\n".join(lines)


def _client(model: str, *, fake: bool) -> LLMClient:
    if fake:
        return FakeLLM()
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("Defina LLM_API_KEY para comparar modelos reais (ou use --fake).")
    effort = os.environ.get("LLM_EFFORT", "low")
    return AnthropicLLMClient(model=model, api_key=key, effort=effort)


async def compare(models: Sequence[str], *, fake: bool = False) -> list[ModelResult]:
    cases = load_cases()
    results = []
    for model in models:
        client = _client(model, fake=fake)
        reports: list[TurnReport] = []
        for case in cases:
            reports += await run_case(case, client if case.llm == "ok" else None)
        results.append(summarize(model, cases, reports))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.golden.compare")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--fake", action="store_true", help="ensaio com FakeLLM, sem rede")
    args = parser.parse_args(argv)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results = asyncio.run(compare(models, fake=args.fake))
    table = render_table(results, fake=args.fake)
    print(table)  # noqa: T201
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(table + "\n", encoding="utf-8")
    return 0 if choose(results) else 1


if __name__ == "__main__":
    sys.exit(main())
