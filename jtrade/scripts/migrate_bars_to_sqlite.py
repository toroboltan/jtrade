"""One-time backfill: state/bars_cache/*.parquet -> the SQLite bars DB.

Run this once, before the incremental-fetch BarProvider is used for real, so the
first scan sees existing history instead of cold-starting every symbol (which would
hammer IBKR's ~50 req/10min historical-data pacing limit).

Does NOT delete the source parquet files — remove state/bars_cache/ manually once
you've confirmed state/bars.db looks right.

Usage: uv run python scripts/migrate_bars_to_sqlite.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jtrade.config import load_settings  # noqa: E402
from jtrade.data import db  # noqa: E402

_FILENAME_RE = re.compile(r"^(?P<symbol>[A-Za-z0-9.\-]+)_(?P<date>\d{4}-\d{2}-\d{2})\.parquet$")


def _group_by_symbol(cache_dir: Path) -> dict[str, list[Path]]:
    """Group parquet files by symbol, each symbol's list sorted oldest -> newest
    filename-date so upserting in order lets the freshest snapshot win on overlap."""
    groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for p in cache_dir.glob("*.parquet"):
        m = _FILENAME_RE.match(p.name)
        if not m:
            continue
        groups[m.group("symbol").upper()].append((m.group("date"), p))
    return {sym: [p for _, p in sorted(entries)] for sym, entries in groups.items()}


def main() -> None:
    settings = load_settings()
    cache_dir = settings.project_root / "state" / "bars_cache"
    if not cache_dir.exists():
        print(f"No parquet cache found at {cache_dir}, nothing to migrate.")
        return

    groups = _group_by_symbol(cache_dir)
    conn = db.connect(settings.db_path)

    migrated, skipped = 0, 0
    for symbol, paths in sorted(groups.items()):
        rows_written = 0
        any_ok = False
        for p in paths:
            try:
                df = pd.read_parquet(p)
            except Exception as e:
                print(f"  skip unreadable {p.name}: {type(e).__name__}: {e}")
                continue
            if df is None or df.empty:
                continue
            rows_written = db.upsert_bars(conn, symbol, df)  # each call reports total rows in this df
            any_ok = True
        if not any_ok:
            skipped += 1
            continue
        print(f"  {symbol}: merged from {len(paths)} snapshot(s), latest write {rows_written} rows")
        migrated += 1

    conn.close()
    print(f"\nDone. {migrated} symbols migrated, {skipped} skipped (no readable data).")
    print(f"DB: {settings.db_path}")
    print(f"Parquet cache left untouched at {cache_dir} — remove it manually once verified.")


if __name__ == "__main__":
    main()
