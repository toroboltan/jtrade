"""Risk engine: sizing math, stop placement, and portfolio-cap enforcement."""

from __future__ import annotations

import pandas as pd

from jtrade import risk
from jtrade.screen import ScreenResult, ScreenRow
from tests.conftest import make_bars


def _rows(specs):
    """specs: list of (ticker, price, signal, strength, tradable, bars)."""
    return [ScreenRow(sheet="s", ticker=t, price=p, change_pct=0.0, stage=2,
                      signal=sig, strength=st, tradable=tr, bars=b)
            for (t, p, sig, st, tr, b) in specs]


def _result(rows):
    return ScreenResult(rows=rows, by_sheet={}, generated_at=pd.Timestamp.now())


def test_size_position_one_percent():
    # 1% of 100k = $1000 risk; risk/share = 10 -> 100 shares
    assert risk.size_position(100_000, entry=100, stop=90, risk_frac=0.01) == 100
    # risk_per_share <= 0 -> 0 shares
    assert risk.size_position(100_000, entry=100, stop=100, risk_frac=0.01) == 0
    assert risk.size_position(100_000, entry=100, stop=110, risk_frac=0.01) == 0


def test_stop_below_ma(settings):
    df = make_bars("MA", start=80, end=120, n=300, seed=2, noise=0.3)
    entry = float(df["Close"].iloc[-1])
    stop, method = risk.compute_stop(df, entry, settings.risk["stop"])
    assert stop is not None and stop < entry
    assert method in ("ma_below", "atr")


def test_buy_sizing_targets_one_percent(settings):
    df = make_bars("AAPL", start=150, end=200, n=300, seed=2, noise=0.3)
    res = _result(_rows([("AAPL", 200.0, "Buy", 0.9, True, df)]))
    props = risk.build_proposals(res, equity=100_000, cash=50_000, positions={}, settings=settings)
    buy = next(p for p in props if p.ticker == "AAPL")
    assert buy.quantity > 0
    # risk amount should be within a share's worth of 1% of equity
    assert abs(buy.risk_amount - 1000) <= buy.risk_per_share + 1


def test_sell_flattens_position(settings):
    df = make_bars("XLE", start=120, end=90, n=300, seed=4, noise=0.3)
    res = _result(_rows([("XLE", 90.0, "Sell", 0.7, True, df)]))
    props = risk.build_proposals(res, 100_000, 50_000, positions={"XLE": 37.0}, settings=settings)
    sell = next(p for p in props if p.ticker == "XLE")
    assert sell.action == "SELL" and sell.quantity == 37 and not sell.reference_only


def test_sell_without_position_is_reference_only(settings):
    df = make_bars("XLE", start=120, end=90, n=300, seed=4, noise=0.3)
    res = _result(_rows([("XLE", 90.0, "Sell", 0.7, True, df)]))
    props = risk.build_proposals(res, 100_000, 50_000, positions={}, settings=settings)
    sell = next(p for p in props if p.ticker == "XLE")
    assert sell.reference_only is True


def test_monitor_only_excluded(settings):
    df = make_bars("LTC", start=80, end=120, n=300, seed=5, noise=0.3)
    res = _result(_rows([("LTC", 100.0, "Buy", 0.9, False, df)]))
    props = risk.build_proposals(res, 100_000, 50_000, {}, settings)
    assert all(p.ticker != "LTC" for p in props)


def test_max_new_proposals_cap(settings):
    specs = [(f"T{i:02d}", 50.0, "Buy", 0.9 - i * 0.02, True,
              make_bars(f"T{i}", start=40, end=60, n=300, seed=i, noise=0.3)) for i in range(15)]
    res = _result(_rows(specs))
    props = risk.build_proposals(res, equity=1_000_000, cash=1_000_000, positions={}, settings=settings)
    kept = [p for p in props if not p.dropped and not p.reference_only]
    assert len(kept) <= settings.risk["max_new_proposals"]
    # highest-strength survive
    assert "T00" in {p.ticker for p in kept}


def test_aggregate_risk_cap(settings):
    # tiny equity so aggregate risk (15%) bites before the count cap
    specs = [(f"R{i:02d}", 50.0, "Buy", 0.9 - i * 0.05, True,
              make_bars(f"R{i}", start=40, end=60, n=300, seed=i, noise=0.3)) for i in range(10)]
    res = _result(_rows(specs))
    equity = 20_000
    props = risk.build_proposals(res, equity=equity, cash=1_000_000, positions={}, settings=settings)
    kept = [p for p in props if not p.dropped and not p.reference_only and p.action == "BUY"]
    total_risk = sum(p.risk_amount for p in kept)
    assert total_risk <= settings.risk["max_aggregate_risk"] * equity + 1e-6


def test_cash_reserve_cap(settings):
    specs = [(f"C{i:02d}", 100.0, "Buy", 0.9 - i * 0.05, True,
              make_bars(f"C{i}", start=80, end=120, n=300, seed=i, noise=0.3)) for i in range(10)]
    res = _result(_rows(specs))
    cash = 20_000
    props = risk.build_proposals(res, equity=1_000_000, cash=cash, positions={}, settings=settings)
    kept = [p for p in props if not p.dropped and not p.reference_only and p.action == "BUY"]
    spent = sum(p.est_cost for p in kept)
    assert cash - spent >= settings.risk["min_cash_reserve"] - 1e-6
