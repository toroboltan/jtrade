"""Unified daily-bar retrieval: IBKR primary, yfinance fallback, SQLite store.

``BarProvider.get_daily_bars(symbol)`` returns a DataFrame indexed by date with
Open/High/Low/Close/Volume, backed by a SQLite database (``data.db_path``) that
accumulates each symbol's full history over time. Each call only fetches the
*missing* days since the latest date already stored for that symbol (comparing
against what IBKR/yfinance has available), instead of re-pulling the whole
``history_years`` window every time — this respects IBKR pacing (~50 historical
requests / 10 min, throttled via ``data.hist_pacing_sleep``) and keeps daily scans fast.
A gap larger than ~6 months (e.g. a newly added symbol, or a long-stale DB) falls back
to a full cold-start refetch rather than computing an exact month/year duration string.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from ..config import Settings
from . import db

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]
_STALE_GAP_DAYS = 185


class BarProvider:
    def __init__(self, settings: Settings, ib_client=None):
        self.settings = settings
        self.ib = ib_client
        self.db_path = settings.db_path
        self.years = int(settings.data.get("history_years", 5))
        self.source_order = list(settings.data.get("source_order", ["ibkr", "yfinance"]))
        self.yf_enabled = bool(settings.data.get("yfinance_fallback", True))
        self.pacing = float(settings.data.get("hist_pacing_sleep", 1.0))
        self._last_ibkr_req = 0.0

    # ---- sources ----
    def _from_ibkr(self, symbol: str, since: date | None) -> pd.DataFrame:
        if self.ib is None or not self.ib.is_connected():
            return pd.DataFrame()
        duration = self._duration_for_gap(since)
        if duration == "":
            return pd.DataFrame()  # already current, no request needed
        # throttle historical requests
        wait = self.pacing - (time.monotonic() - self._last_ibkr_req)
        if wait > 0:
            time.sleep(wait)
        if duration is None:
            df = self.ib.daily_bars(symbol, years=self.years)
        else:
            df = self.ib.daily_bars(symbol, duration=duration)
        self._last_ibkr_req = time.monotonic()
        return df

    def _from_yfinance(self, symbol: str, since: date | None) -> pd.DataFrame:
        if not self.yf_enabled:
            return pd.DataFrame()
        try:
            import yfinance as yf
        except ImportError:
            return pd.DataFrame()
        gap_days = None if since is None else (date.today() - since).days
        if gap_days is not None and gap_days <= 0:
            return pd.DataFrame()  # already current
        try:
            if since is not None and (gap_days is None or gap_days <= _STALE_GAP_DAYS):
                hist = yf.Ticker(symbol).history(start=since + timedelta(days=1))
            else:
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

    def _duration_for_gap(self, since: date | None) -> str | None:
        """Return an IBKR durationStr for the gap since `since`, "" if already
        current (no fetch needed), or None to signal a full cold-start refetch."""
        if since is None:
            return None
        gap_days = (date.today() - since).days
        if gap_days <= 0:
            return ""
        if gap_days > _STALE_GAP_DAYS:
            return None
        return f"{gap_days + 1} D"

    # ---- public API ----
    def get_daily_bars(self, symbol: str, use_cache: bool = True) -> pd.DataFrame:
        conn = db.connect(self.db_path)
        try:
            since = db.latest_date(conn, symbol) if use_cache else None

            fresh = pd.DataFrame()
            for source in self.source_order:
                if source == "ibkr":
                    fresh = self._from_ibkr(symbol, since)
                elif source == "yfinance":
                    fresh = self._from_yfinance(symbol, since)
                if fresh is not None and not fresh.empty:
                    break

            if fresh is not None and not fresh.empty:
                fresh = fresh[[c for c in _OHLCV if c in fresh.columns]].sort_index()
                db.upsert_bars(conn, symbol, fresh)

            return db.read_bars(conn, symbol)
        finally:
            conn.close()
