"""Weinstein-style Stage Analysis — port of tktStageAnalysis/stageAnalyzer.py:21-142.

Faithful to the original stage/signal rules, with two latent bugs fixed:
  1. deprecated ``fillna(method='ffill')`` -> ``.ffill()``
  2. ``get_current_stage()`` crashed on a leading-NaN / short-history current stage
     (``int(NaN)`` and a NaN dict lookup). Here the latest stage is simply NaN -> Hold.

Adds a ``strength`` score in [0, 1] for ranking proposals, composed of:
  - transition recency (fresher stage change scores higher)
  - relative strength vs a benchmark (e.g. SPY) over a trailing window
  - RSI posture appropriate to the stage (bullish stages reward RSI>50, bearish <50)
"""

from __future__ import annotations

import math
from typing import ClassVar

import numpy as np
import pandas as pd

from .base import Criterion

NAME = "stage_analysis"

STAGE_DESCRIPTIONS = {
    1: "Accumulation (Bottoming/Basing)",
    2: "Markup (Uptrend/Bull)",
    3: "Distribution (Topping)",
    4: "Decline (Downtrend/Bear)",
}


class StageAnalysis(Criterion):
    name: ClassVar[str] = NAME

    def __init__(self, **config):
        super().__init__(**config)
        self.ma_short = int(config.get("ma_short", 50))
        self.ma_long = int(config.get("ma_long", 200))
        self.rsi_period = int(config.get("rsi_period", 14))
        # strength weights (sum need not be 1; normalized below)
        self.w_recency = float(config.get("w_recency", 0.4))
        self.w_relstr = float(config.get("w_relstr", 0.35))
        self.w_rsi = float(config.get("w_rsi", 0.25))
        self.recency_halflife = int(config.get("recency_halflife", 15))  # bars
        self.relstr_window = int(config.get("relstr_window", 60))         # bars

    # ---- indicators (ported) ----
    def _indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["MA_Short"] = df["Close"].rolling(window=self.ma_short).mean()
        df["MA_Long"] = df["Close"].rolling(window=self.ma_long).mean()

        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).fillna(0)
        loss = -delta.where(delta < 0, 0).fillna(0)
        avg_gain = gain.rolling(window=self.rsi_period).mean()
        avg_loss = loss.rolling(window=self.rsi_period).mean()
        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))
        if "Volume" in df:
            df["Volume_MA"] = df["Volume"].rolling(window=20).mean()
        return df

    # ---- stages (ported, ffill fixed) ----
    def _stages(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["Stage"] = np.nan

        cond1 = (
            (df["MA_Short"] > df["MA_Short"].shift(1))
            & (df["MA_Long"] < df["MA_Long"].shift(1))
            & (df["RSI"] > 50)
        )
        cond2 = (
            (df["MA_Short"] > df["MA_Long"])
            & (df["MA_Short"] > df["MA_Short"].shift(1))
            & (df["MA_Long"] > df["MA_Long"].shift(1))
        )
        cond3 = (
            (df["MA_Short"] < df["MA_Short"].shift(1))
            & (df["MA_Long"] > df["MA_Long"].shift(1))
            & (df["RSI"] < 50)
        )
        cond4 = (
            (df["MA_Short"] < df["MA_Long"])
            & (df["MA_Short"] < df["MA_Short"].shift(1))
            & (df["MA_Long"] < df["MA_Long"].shift(1))
        )
        df.loc[cond1, "Stage"] = 1
        df.loc[cond2, "Stage"] = 2
        df.loc[cond3, "Stage"] = 3
        df.loc[cond4, "Stage"] = 4
        df["Stage"] = df["Stage"].ffill()   # BUGFIX: was fillna(method='ffill')
        return df

    # ---- signals (ported) ----
    def _signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["Signal"] = "Hold"
        buy = (df["Stage"] == 2) & (df["Stage"].shift(1) == 1)
        sell = (df["Stage"] == 4) & (df["Stage"].shift(1) == 3)
        df.loc[buy, "Signal"] = "Buy"
        df.loc[sell, "Signal"] = "Sell"
        return df

    # ---- strength scoring ----
    def _bars_since_stage_change(self, stage: pd.Series) -> pd.Series:
        # counts rows since the stage last differed from the current run
        changed = stage.ne(stage.shift(1))
        group = changed.cumsum()
        return stage.groupby(group).cumcount()

    def _rel_strength(self, close: pd.Series) -> pd.Series:
        """Trailing-window return minus benchmark trailing return, squashed to [0,1]."""
        w = self.relstr_window
        sym_ret = close.pct_change(w)
        if self._benchmark is not None and len(self._benchmark) > w:
            bench = self._benchmark.reindex(close.index).ffill()
            bench_ret = bench.pct_change(w)
            diff = sym_ret - bench_ret
        else:
            diff = sym_ret  # absolute momentum when no benchmark available
        # logistic squash; 10% outperformance -> ~0.73
        return diff.apply(lambda x: 1.0 / (1.0 + math.exp(-8.0 * x)) if pd.notna(x) else np.nan)

    def _strength(self, df: pd.DataFrame) -> pd.Series:
        stage = df["Stage"]
        bars_since = self._bars_since_stage_change(stage)
        recency = np.exp(-bars_since / max(self.recency_halflife, 1))  # 1.0 at a fresh change

        relstr = self._rel_strength(df["Close"])

        rsi = df["RSI"] / 100.0
        # bullish stages (1,2) reward RSI>0.5; bearish (3,4) reward RSI<0.5
        bullish = stage.isin([1, 2])
        rsi_posture = np.where(bullish, rsi, 1.0 - rsi)
        rsi_posture = pd.Series(rsi_posture, index=df.index)

        wsum = self.w_recency + self.w_relstr + self.w_rsi
        strength = (
            self.w_recency * recency.fillna(0.0)
            + self.w_relstr * relstr.fillna(0.5)
            + self.w_rsi * rsi_posture.fillna(0.5)
        ) / wsum
        return strength.clip(0.0, 1.0)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "Close" not in df:
            return pd.DataFrame(columns=["Stage", "Signal", "strength"])
        df = df.sort_index()
        df = self._indicators(df)
        df = self._stages(df)
        df = self._signals(df)
        df["strength"] = self._strength(df)
        return df
