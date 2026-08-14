"""Moving Average Road Map: % distance from price to each EMA/SMA."""

from __future__ import annotations

import pandas as pd
import pytest

from jtrade.indicators import MA_ROADMAP, ma_pct_diffs
from tests.conftest import make_bars

_KEYS = [key for key, *_ in MA_ROADMAP]


def test_all_keys_present():
    df = make_bars("FULL", start=100, end=100, n=400, seed=1, noise=0.0)
    diffs = ma_pct_diffs(df["Close"])
    assert set(diffs.keys()) == set(_KEYS)


def test_flat_series_diffs_near_zero():
    df = make_bars("FLAT", start=100, end=100, n=400, seed=2, noise=0.0)
    diffs = ma_pct_diffs(df["Close"])
    for key in _KEYS:
        assert diffs[key] is not None
        assert abs(diffs[key]) < 1e-6


def test_short_history_long_windows_none():
    df = make_bars("SHORT", start=100, end=110, n=30, seed=3, noise=0.5)
    diffs = ma_pct_diffs(df["Close"])
    for key, _label, _kind, period, _desc in MA_ROADMAP:
        if period > 30:
            assert diffs[key] is None
        else:
            assert diffs[key] is not None


def test_empty_series_returns_all_none():
    diffs = ma_pct_diffs(pd.Series(dtype=float))
    assert diffs == {key: None for key in _KEYS}


def test_diff_formula_matches_manual_calc():
    df = make_bars("CALC", start=50, end=150, n=300, seed=4, noise=0.6)
    close = df["Close"]
    diffs = ma_pct_diffs(close)

    ema5 = close.ewm(span=5, adjust=False).mean().iloc[-1]
    expected_ema5 = (close.iloc[-1] - ema5) / ema5 * 100.0
    assert diffs["ema5"] == pytest.approx(expected_ema5, abs=1e-9)

    sma200 = close.rolling(200).mean().iloc[-1]
    expected_sma200 = (close.iloc[-1] - sma200) / sma200 * 100.0
    assert diffs["sma200"] == pytest.approx(expected_sma200, abs=1e-9)
