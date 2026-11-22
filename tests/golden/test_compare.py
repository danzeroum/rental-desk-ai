"""Comparador de modelos: escolha, percentil e tabela (sem rede)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.golden.compare import ModelResult, choose, main, percentile, render_table

BASE = ModelResult(
    model="m", cases=41, turns=54, turns_ok=54, fact_violations=0, cards=25,
    cards_with_notice=25, reservation_claims=0, handoff_accuracy=1.0, p95_seconds=3.0,
    cost_per_conversation_usd=0.01, refusal_fallbacks=0,
)  # fmt: skip


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 95) == 19.0
    assert percentile([2.0], 95) == 2.0
    assert percentile([], 95) == 0.0


def test_chooses_cheapest_that_passes_everything() -> None:
    haiku = replace(BASE, model="haiku", cost_per_conversation_usd=0.001, handoff_accuracy=0.9)
    sonnet = replace(BASE, model="sonnet", cost_per_conversation_usd=0.02)
    opus = replace(BASE, model="opus", cost_per_conversation_usd=0.04)
    assert choose([haiku, sonnet, opus]) == sonnet
    assert haiku.failed_criteria == ["passagem < 95%"]


def test_slow_model_fails_spec_p95() -> None:
    slow = replace(BASE, p95_seconds=15.1)
    assert slow.failed_criteria == ["p95 > 15 s"]
    assert choose([slow]) is None


def test_any_fact_outside_base_fails() -> None:
    assert not replace(BASE, fact_violations=1).approved
    assert not replace(BASE, cards_with_notice=24).approved
    assert not replace(BASE, reservation_claims=1).approved


def test_table_shows_recommendation() -> None:
    table = render_table([replace(BASE, model="claude-haiku-5-5")], fake=False)
    assert "| `claude-haiku-5-5` | 54/54 (100%) |" in table
    assert "**Recomendação:** `claude-haiku-5-5`" in table


def test_fake_dry_run_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "comparacao.md"
    assert main(["--fake", "--models", "fake-a,fake-b", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "`fake-a`" in text and "`fake-b`" in text
    assert "Ensaio com FakeLLM" in text
