"""Unified daily-bar retrieval: IBKR primary, yfinance fallback, parquet cache.

``BarProvider.get_daily_bars(symbol)`` returns a DataFrame indexed by date with
Open/High/Low/Close/Volume. Same-day bars are served from an on-disk parquet cache to
respect IBKR pacing (~50 historical requests / 10 min); live IBKR requests are throttled
by ``data.hist_pacing_sleep`` seconds. Source order comes from ``data.source_order``.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd

from ..config import Settings

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class BarProvider:
    def __init__(self, settings: Settings, ib_client=None):
        self.settings = settings
        self.ib = ib_client
        self.cache_dir: Path = settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.years = int(settings.data.get("history_years", 5))
        self.source_order = list(settings.data.get("source_order", ["ibkr", "yfinance"]))
        self.yf_enabled = bool(settings.data.get("yfinance_fallback", True))
        self.pacing = float(settings.data.get("hist_pacing_sleep", 1.0))
        self._last_ibkr_req = 0.0

    # ---- cache helpers ----
    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.upper()}_{date.today().isoformat()}.parquet"

    def _read_cache(self, symbol: str) -> pd.DataFrame | None:
        p = self._cache_path(symbol)
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception:
                return None
        return None

    def _write_cache(self, symbol: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        try:
            df.to_parquet(self._cache_path(symbol))
        except Exception:
            pass  # cache is best-effort; never fail a scan on a cache write

    # ---- sources ----
    def _from_ibkr(self, symbol: str) -> pd.DataFrame:
        if self.ib is None or not self.ib.is_connected():
            return pd.DataFrame()
        # throttle historical requests
        wait = self.pacing - (time.monotonic() - self._last_ibkr_req)
        if wait > 0:
            time.sleep(wait)
        df = self.ib.daily_bars(symbol, years=self.years)
        self._last_ibkr_req = time.monotonic()
        return df

    def _from_yfinance(self, symbol: str) -> pd.DataFrame:
        if not self.yf_enabled:
            return pd.DataFrame()
        try:
            import yfinance as yf
        except ImportError:
            return pd.DataFrame()
        try:
            hist = yf.Ticker(symbol).history(period=f"{self.years}y")
        except Exception:
            return pd.DataFrame()
        if hist is None or hist.empty:
            return pd.DataFrame()
        hist = hist.rename(columns=str.title)  # already title-case, defensive
        if getattr(hist.index, "tz", None) is not None:
            hist.index = hist.index.tz_localize(None)
        cols = [c for c in _OHLCV if c in hist.columns]
        return hist[cols]

    # ---- public API ----
    def get_daily_bars(self, symbol: str, use_cache: bool = True) -> pd.DataFrame:
        if use_cache:
            cached = self._read_cache(symbol)
            if cached is not None and not cached.empty:
                return cached

        df = pd.DataFrame()
        for source in self.source_order:
            if source == "ibkr":
                df = self._from_ibkr(symbol)
            elif source == "yfinance":
                df = self._from_yfinance(symbol)
            if df is not None and not df.empty:
                break

        if df is not None and not df.empty:
            df = df[[c for c in _OHLCV if c in df.columns]].sort_index()
            self._write_cache(symbol, df)
            return df
        return pd.DataFrame()
