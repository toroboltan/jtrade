"""run_screen: bars-only pricing (no ib_client argument, no live snapshot)."""

from __future__ import annotations

import pandas as pd
import pytest

from jtrade.screen import run_screen
from jtrade.universe import Symbol, Universe
from tests.conftest import make_bars


class FakeProvider:
    """Serves pre-built bars for a fixed set of symbols; no IBKR/yfinance involved."""

    def __init__(self, bars: dict[str, pd.DataFrame]):
        self.bars = bars

    def get_daily_bars(self, symbol, use_cache=True):
        return self.bars.get(symbol, pd.DataFrame())


def _universe(tickers):
    symbols = {t: Symbol(ticker=t, sheets=["sheet1"], tradable=True) for t in tickers}
    return Universe(by_sheet={"sheet1": tickers}, symbols=symbols)


def test_run_screen_signature_has_no_ib_client(settings):
    bars = {"SPY": make_bars("SPY", n=250), "AAPL": make_bars("AAPL", n=250)}
    provider = FakeProvider(bars)
    universe = _universe(["SPY", "AAPL"])

    result = run_screen(settings, provider, universe=universe)

    with pytest.raises(TypeError):
        run_screen(settings, provider, ib_client=object(), universe=universe)

    assert {r.ticker for r in result.rows} == {"SPY", "AAPL"}


def test_price_and_change_come_from_bars_not_snapshot(settings):
    df = make_bars("AAPL", start=150, end=200, n=250, seed=2, noise=0.3)
    provider = FakeProvider({"AAPL": df, "SPY": make_bars("SPY", n=250)})
    universe = _universe(["AAPL", "SPY"])

    result = run_screen(settings, provider, universe=universe)

    row = next(r for r in result.rows if r.ticker == "AAPL")
    last, prev = df["Close"].iloc[-1], df["Close"].iloc[-2]
    expected_change = (last - prev) / prev * 100.0

    assert row.price == pytest.approx(float(last))
    assert row.change_pct == pytest.approx(expected_change)
