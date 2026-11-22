from datetime import UTC, date, datetime

from app.web.charts import HEIGHT, bar_chart, daily_counts


def test_daily_counts_use_sao_paulo_days() -> None:
    moments = [datetime(2026, 12, 2, 2, 0, tzinfo=UTC), datetime(2026, 12, 2, 15, 0, tzinfo=UTC)]
    assert daily_counts(moments, date(2026, 12, 1), 3) == [
        (date(2026, 12, 1), 1),  # 23h do dia 1 em São Paulo
        (date(2026, 12, 2), 1),
        (date(2026, 12, 3), 0),
    ]


def test_bar_chart_geometry() -> None:
    series = [(date(2026, 12, d), v) for d, v in [(1, 0), (2, 7), (3, 3)]]
    chart = bar_chart(series, "contatos")
    assert chart.max_value == 10 and chart.baseline_y == HEIGHT - 24
    assert chart.bars[0].path == ""  # zero não desenha barra
    assert chart.bars[1].path.startswith("M") and chart.bars[1].label == "02/12: 7 contatos"
    assert [(label, anchor) for _, label, anchor in chart.ticks] == [
        ("01/12", "start"),
        ("02/12", "middle"),
        ("03/12", "end"),
    ]


def test_empty_series() -> None:
    chart = bar_chart([], "contatos")
    assert chart.bars == [] and chart.max_value == 1


def test_nice_max() -> None:
    from app.web.charts import _nice_max  # noqa: PLC0415

    assert [_nice_max(v) for v in (0, 3, 7, 23, 120, 999)] == [1, 3, 10, 25, 200, 1000]
