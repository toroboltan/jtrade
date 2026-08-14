"""Moving Average Road Map — price vs. a fixed set of EMA/SMA windows.

Purely informational: percentage distance between the latest close and each
road-map moving average, for display alongside (not feeding into) Stage Analysis.
"""

from __future__ import annotations

import pandas as pd

# (key, short_label, kind, period, description)
MA_ROADMAP: list[tuple[str, str, str, int, str]] = [
    ("ema5", "5D EMA", "ema", 5, "Strong Momentum"),
    ("ema10", "10D EMA", "ema", 10, "Short-Term Trend"),
    ("ema20", "20D EMA", "ema", 20, "Pullback Support"),
    ("sma50", "50D SMA", "sma", 50, "Uptrend Defense Line"),
    ("sma100", "100D SMA", "sma", 100, "Big Price Dip"),
    ("sma200", "200D SMA", "sma", 200, "Bull/Bear Boundary"),
    ("sma250", "250D SMA", "sma", 250, "Value Zone"),
]


def ma_pct_diffs(close: pd.Series) -> dict[str, float | None]:
    """(price - MA)/MA * 100 for each MA_ROADMAP entry; None if not enough history."""
    if close is None or close.empty:
        return {key: None for key, *_ in MA_ROADMAP}

    last = close.iloc[-1]
    out: dict[str, float | None] = {}
    for key, _label, kind, period, _desc in MA_ROADMAP:
        if len(close) < period:
            out[key] = None
            continue
        if kind == "ema":
            ma = close.ewm(span=period, adjust=False).mean().iloc[-1]
        else:
            ma = close.rolling(period).mean().iloc[-1]
        out[key] = None if pd.isna(ma) or ma == 0 else float((last - ma) / ma * 100.0)
    return out
