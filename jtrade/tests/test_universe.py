"""Universe loader: de-dupe + tradable/monitor classification against the real workbook."""

from __future__ import annotations

from jtrade.universe import load_universe


def test_dedupe_and_counts(settings):
    u = load_universe(settings)
    assert len(u.by_sheet) == 19
    # 197 unique symbols confirmed from the workbook
    assert len(u.symbols) == 197
    # SPY appears on multiple sheets but is one de-duped symbol
    assert set(["MainMetrics", "indexes"]).issubset(set(u.symbols["SPY"].sheets))


def test_monitor_only_classification(settings):
    u = load_universe(settings)
    # explicit monitor-only symbols
    assert u.symbols["VIX"].tradable is False
    assert u.symbols["LTC"].tradable is False
    # a symbol only on the monitor-only `indexes` sheet is monitor-only
    assert u.symbols["ONEQ"].tradable is False
    # a plain sector ETF is tradable
    assert u.symbols["XLK"].tradable is True
    # SPY appears on tradable sheets too -> tradable despite also being on `indexes`
    assert u.symbols["SPY"].tradable is True


def test_tradable_and_monitor_partition(settings):
    u = load_universe(settings)
    assert set(u.tradable).isdisjoint(set(u.monitor_only))
    assert len(u.tradable) + len(u.monitor_only) == len(u.symbols)
