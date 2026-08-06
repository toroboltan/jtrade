"""Shared fixtures + synthetic bar helpers (IBKR/yfinance are not reachable in CI)."""

from __future__ import annotations

import copy
import hashlib

import numpy as np
import pandas as pd
import pytest

from jtrade.config import Settings, load_settings


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def settings_tmp_db(settings, tmp_path):
    """Settings with data.db_path redirected to a throwaway SQLite file per test."""
    raw = copy.deepcopy(settings.raw)
    raw["data"]["db_path"] = str(tmp_path / "bars_test.db")
    return Settings(raw=raw, path=settings.path)


def make_bars(symbol="SYM", start=100.0, end=100.0, n=400, seed=None, noise=0.8) -> pd.DataFrame:
    """Deterministic synthetic OHLCV. Linear trend from `start`->`end` plus noise."""
    if seed is None:
        seed = int(hashlib.md5(symbol.encode()).hexdigest(), 16) % (2**32)
    rng = np.random.default_rng(seed)
    close = np.linspace(start, end, n) + rng.normal(0, noise, n)
    close = np.abs(close) + 1.0
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
         "Volume": rng.integers(1_000_000, 5_000_000, n)},
        index=idx,
    )
