"""One-time parquet -> SQLite migration script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from jtrade.data import db
from tests.conftest import make_bars

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "migrate_bars_to_sqlite.py"


def _load_migrate_module():
    spec = importlib.util.spec_from_file_location("migrate_bars_to_sqlite", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bars(start_day: int, n: int, close: float) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=start_day + n)[start_day:]
    vals = [close] * n
    return pd.DataFrame(
        {"Open": vals, "High": vals, "Low": vals, "Close": vals, "Volume": [1000] * n},
        index=idx,
    )


def _run_migration(tmp_path, settings_tmp_db, monkeypatch):
    mod = _load_migrate_module()

    class FakeSettings:
        project_root = tmp_path
        db_path = settings_tmp_db.db_path

    monkeypatch.setattr(mod, "load_settings", lambda: FakeSettings())
    mod.main()
    return mod


def test_migrate_prefers_freshest_snapshot_even_with_fewer_rows(
    settings_tmp_db, monkeypatch, tmp_path
):
    """Reproduces a real bug found in production: a file with MORE rows but an
    OLDER last date must not beat a file with FEWER rows but a NEWER last date."""
    cache_dir = tmp_path / "state" / "bars_cache"
    cache_dir.mkdir(parents=True)

    # "stale but big": 1000 rows, ending earlier
    stale_big = _bars(start_day=0, n=1000, close=100.0)
    # "fresh but small": 998 rows, starting 3 business days later -> ends 3 days later
    fresh_small = _bars(start_day=3, n=998, close=200.0)
    assert stale_big.index[-1] < fresh_small.index[-1]
    assert len(stale_big) > len(fresh_small)

    stale_big.to_parquet(cache_dir / "AAPL_2026-07-20.parquet")   # older filename date
    fresh_small.to_parquet(cache_dir / "AAPL_2026-07-27.parquet")  # newer filename date

    _run_migration(tmp_path, settings_tmp_db, monkeypatch)

    conn = db.connect(settings_tmp_db.db_path)
    aapl = db.read_bars(conn, "AAPL")
    conn.close()

    assert aapl.index[-1] == fresh_small.index[-1]  # freshest last date wins
    # overlapping dates must reflect the fresher file's values, not the stale one's
    overlap_date = fresh_small.index[0]
    assert aapl.loc[overlap_date, "Close"] == 200.0


def test_migrate_leaves_originals_and_skips_unreadable(settings_tmp_db, monkeypatch, tmp_path):
    cache_dir = tmp_path / "state" / "bars_cache"
    cache_dir.mkdir(parents=True)

    spy = make_bars("SPY", n=20, seed=2)
    spy.to_parquet(cache_dir / "SPY_2026-07-27.parquet")

    unreadable = cache_dir / "MSFT_2026-07-27.parquet"
    unreadable.write_text("not a real parquet file")

    _run_migration(tmp_path, settings_tmp_db, monkeypatch)

    conn = db.connect(settings_tmp_db.db_path)
    spy_out = db.read_bars(conn, "SPY")
    msft_out = db.read_bars(conn, "MSFT")
    conn.close()

    assert len(spy_out) == 20
    assert msft_out.empty  # unreadable file skipped, not fatal
    assert (cache_dir / "SPY_2026-07-27.parquet").exists()  # originals untouched
    assert unreadable.exists()
