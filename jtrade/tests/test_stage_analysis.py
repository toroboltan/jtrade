"""Stage-analysis plugin: parity with the original stageAnalyzer formulas + signal rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from jtrade.criteria.stage_analysis import StageAnalysis
from tests.conftest import make_bars


def _original_stages(df, ma_short=50, ma_long=200, rsi_period=14):
    """Replica of tktStageAnalysis/stageAnalyzer.py:21-76 (indicators + stages)."""
    d = df.copy()
    d["MA_Short"] = d["Close"].rolling(ma_short).mean()
    d["MA_Long"] = d["Close"].rolling(ma_long).mean()
    delta = d["Close"].diff()
    gain = delta.where(delta > 0, 0).fillna(0)
    loss = -delta.where(delta < 0, 0).fillna(0)
    rs = gain.rolling(rsi_period).mean() / loss.rolling(rsi_period).mean()
    d["RSI"] = 100 - (100 / (1 + rs))
    d["Stage"] = np.nan
    c1 = (d["MA_Short"] > d["MA_Short"].shift(1)) & (d["MA_Long"] < d["MA_Long"].shift(1)) & (d["RSI"] > 50)
    c2 = (d["MA_Short"] > d["MA_Long"]) & (d["MA_Short"] > d["MA_Short"].shift(1)) & (d["MA_Long"] > d["MA_Long"].shift(1))
    c3 = (d["MA_Short"] < d["MA_Short"].shift(1)) & (d["MA_Long"] > d["MA_Long"].shift(1)) & (d["RSI"] < 50)
    c4 = (d["MA_Short"] < d["MA_Long"]) & (d["MA_Short"] < d["MA_Short"].shift(1)) & (d["MA_Long"] < d["MA_Long"].shift(1))
    d.loc[c1, "Stage"] = 1
    d.loc[c2, "Stage"] = 2
    d.loc[c3, "Stage"] = 3
    d.loc[c4, "Stage"] = 4
    d["Stage"] = d["Stage"].ffill()
    return d["Stage"]


def test_stage_parity_with_original():
    for seed in range(6):
        df = make_bars(f"P{seed}", start=100, end=180, n=600, seed=seed, noise=1.2)
        plugin = StageAnalysis(ma_short=50, ma_long=200, rsi_period=14)
        out = plugin.compute(df)
        orig = _original_stages(df)
        assert (out["Stage"].fillna(-1) == orig.fillna(-1)).all(), f"stage mismatch seed={seed}"


def test_signal_rules_buy_and_sell():
    # construct an explicit 1->2 and 3->4 transition and check signal labels
    df = make_bars("SIG", start=100, end=200, n=600, seed=7, noise=1.0)
    out = StageAnalysis().compute(df)
    # every Buy row must be Stage 2 preceded by Stage 1; Sell must be Stage 4 after 3
    buys = out[out["Signal"] == "Buy"]
    sells = out[out["Signal"] == "Sell"]
    for idx in buys.index:
        i = out.index.get_loc(idx)
        assert out["Stage"].iloc[i] == 2 and out["Stage"].iloc[i - 1] == 1
    for idx in sells.index:
        i = out.index.get_loc(idx)
        assert out["Stage"].iloc[i] == 4 and out["Stage"].iloc[i - 1] == 3


def test_short_history_no_crash():
    # the original get_current_stage() crashed on leading-NaN; ours returns Hold cleanly
    df = make_bars("SHORT", n=20, seed=1)
    out = StageAnalysis().latest(df)
    assert out["Signal"] == "Hold"
    assert out["Stage"] is None  # not enough history for a stage


def test_strength_in_range():
    df = make_bars("STR", start=100, end=200, n=500, seed=3)
    out = StageAnalysis().compute(df)
    s = out["strength"].dropna()
    assert ((s >= 0) & (s <= 1)).all()
