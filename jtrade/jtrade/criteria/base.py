"""Criterion ABC — the extensibility contract for the screening engine.

A criterion takes a single symbol's daily OHLCV history and returns the same frame
with three columns added:
  - ``Stage``   : the criterion's regime label (int/float or NaN)
  - ``Signal``  : one of 'Buy' / 'Sell' / 'Hold'
  - ``strength``: float in roughly [0, 1] used to rank proposals (top-N per scan)

The engine only ever calls ``compute``. Criteria may accept configuration in their
constructor and an optional benchmark series via ``set_benchmark`` (used for
relative-strength scoring); criteria that don't need it can ignore it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd


class Criterion(ABC):
    #: unique registry name; must match the module-level ``NAME`` used for discovery
    name: ClassVar[str] = "base"

    def __init__(self, **config):
        self.config = config
        self._benchmark: pd.Series | None = None

    def set_benchmark(self, close: pd.Series | None) -> None:
        """Provide the benchmark's daily Close series for relative-strength scoring."""
        self._benchmark = close

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Input: daily OHLCV (columns Open/High/Low/Close/Volume, DatetimeIndex).
        Output: same frame with Stage, Signal ('Buy'/'Sell'/'Hold'), strength added."""
        raise NotImplementedError

    # -- convenience used by screen.py --
    def latest(self, df: pd.DataFrame) -> dict:
        """Return the most recent row's Stage/Signal/strength as a plain dict."""
        out = self.compute(df)
        if out.empty:
            return {"Stage": None, "Signal": "Hold", "strength": 0.0}
        row = out.iloc[-1]
        stage = row.get("Stage")
        return {
            "Stage": None if pd.isna(stage) else int(stage),
            "Signal": str(row.get("Signal", "Hold")),
            "strength": float(row.get("strength", 0.0) or 0.0),
        }
