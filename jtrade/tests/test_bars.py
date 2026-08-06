"""BarProvider: incremental SQLite-backed fetch (IBKR primary, yfinance fallback)."""

from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pandas as pd
import pytest

from jtrade.data import db
from jtrade.data.bars import BarProvider
from tests.conftest import make_bars


class FakeIB:
    """Records calls; returns synthetic bars sized to what was requested."""

    def __init__(self, full_df: pd.DataFrame, tail_df: pd.DataFrame | None = None):
        self.full_df = full_df
        self.tail_df = tail_df if tail_df is not None else full_df.iloc[-3:]
        self.calls: list[dict] = []
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    def daily_bars(self, symbol, years=None, duration=None):
        self.calls.append({"symbol": symbol, "years": years, "duration": duration})
        return self.tail_df if duration is not None else self.full_df


def _provider(settings_tmp_db, ib):
    s = settings_tmp_db
    s.data["source_order"] = ["ibkr"]
    s.data["yfinance_fallback"] = False
    return BarProvider(s, ib_client=ib)


def test_cold_start_fetches_full_history_and_stores_it(settings_tmp_db):
    full = make_bars("SYM", n=250)
    ib = FakeIB(full)
    provider = _provider(settings_tmp_db, ib)

    out = provider.get_daily_bars("SYM")

    assert len(ib.calls) == 1
    assert ib.calls[0]["duration"] is None
    assert ib.calls[0]["years"] == provider.years
    assert len(out) == len(full)
    assert out["Close"].iloc[-1] == pytest.approx(full["Close"].iloc[-1])


def test_incremental_fetch_uses_gap_duration(settings_tmp_db):
    full = make_bars("SYM", n=250)
    # Anchor the synthetic series to end today, so the stored/extra split leaves a
    # small, realistic gap (not the multi-year gap the raw 2023-01-01 series would
    # produce against "today"), keeping this within the incremental (non-stale) path.
    shift = pd.Timestamp(date.today()) - full.index[-1]
    full.index = full.index + shift
    stored, extra = full.iloc[:-2], full.iloc[-2:]
    ib = FakeIB(full, tail_df=extra)
    provider = _provider(settings_tmp_db, ib)

    conn = db.connect(provider.db_path)
    db.upsert_bars(conn, "SYM", stored)
    last_stored = stored.index[-1].date()
    conn.close()

    out = provider.get_daily_bars("SYM")

    gap_days = (date.today() - last_stored).days
    assert len(ib.calls) == 1
    assert ib.calls[0]["duration"] == f"{gap_days + 1} D"
    assert len(out) == len(stored) + len(extra)


def test_already_current_is_a_noop(settings_tmp_db):
    full = make_bars("SYM", n=250, end=200.0)
    # Re-index so the last row lands on today, simulating a same-day rerun.
    shift = pd.Timestamp(date.today()) - full.index[-1]
    full.index = full.index + shift
    ib = FakeIB(full)
    provider = _provider(settings_tmp_db, ib)

    conn = db.connect(provider.db_path)
    db.upsert_bars(conn, "SYM", full)
    conn.close()

    out = provider.get_daily_bars("SYM")

    assert ib.calls == []
    assert len(out) == len(full)


def test_upsert_dedupes_overlapping_days(settings_tmp_db):
    conn = db.connect(settings_tmp_db.db_path)
    base = make_bars("SYM", n=10, start=10.0, end=10.0, noise=0.0)
    db.upsert_bars(conn, "SYM", base)

    overlap = base.iloc[-3:].copy()
    overlap["Close"] = overlap["Close"] + 100.0  # distinct value to prove overwrite
    db.upsert_bars(conn, "SYM", overlap)

    out = db.read_bars(conn, "SYM")
    conn.close()

    assert len(out) == len(base)  # no duplicate rows from the 3-day overlap
    assert out["Close"].iloc[-1] == pytest.approx(overlap["Close"].iloc[-1])


def test_stale_db_falls_back_to_full_refetch(settings_tmp_db):
    full = make_bars("SYM", n=250)
    old = make_bars("SYM", n=5, start=50.0, end=50.0, noise=0.0)
    old.index = pd.date_range("2020-01-01", periods=5, freq="B")  # long-stale
    ib = FakeIB(full)
    provider = _provider(settings_tmp_db, ib)

    conn = db.connect(provider.db_path)
    db.upsert_bars(conn, "SYM", old)
    conn.close()

    provider.get_daily_bars("SYM")

    assert len(ib.calls) == 1
    assert ib.calls[0]["duration"] is None
    assert ib.calls[0]["years"] == provider.years


def test_yfinance_incremental_start_param(settings_tmp_db, monkeypatch):
    calls: list[dict] = []

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append(kwargs)
            idx = pd.date_range("2024-06-01", periods=2, freq="B")
            close = [10.0, 11.0]
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close,
                 "Volume": [1000, 1000]},
                index=idx,
            )

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))

    provider = BarProvider(settings_tmp_db, ib_client=None)

    since = date.today() - timedelta(days=5)
    df = provider._from_yfinance("SYM", since)
    assert calls[-1] == {"start": since + timedelta(days=1)}
    assert not df.empty

    df_cold = provider._from_yfinance("SYM", None)
    assert calls[-1] == {"period": f"{provider.years}y"}
    assert not df_cold.empty
