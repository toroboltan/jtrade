"""SQLite persistence for daily OHLCV bars — one row per (symbol, date).

Concurrency: WAL mode + a busy_timeout let the CLI and MCP front-ends (separate
IBKR client IDs, see config/settings.yaml) each open short-lived connections without
hard "database is locked" errors. True concurrent-writer overlap (a CLI scan and an
MCP scan running at the same instant) is not otherwise coordinated — an accepted
limitation for a local, on-demand, single-user tool.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date ON daily_bars(symbol, date);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.executescript(_SCHEMA)
    return conn


def latest_date(conn: sqlite3.Connection, symbol: str) -> date | None:
    row = conn.execute(
        "SELECT MAX(date) FROM daily_bars WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return date.fromisoformat(row[0])


def read_bars(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM daily_bars "
        "WHERE symbol = ? ORDER BY date",
        conn,
        params=(symbol.upper(),),
    )
    if df.empty:
        return pd.DataFrame(columns=_OHLCV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").rename(
        columns={"open": "Open", "high": "High", "low": "Low",
                 "close": "Close", "volume": "Volume"}
    )
    df.index.name = "Date"
    return df[_OHLCV]


def upsert_bars(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    symbol = symbol.upper()
    df = df.dropna(subset=_OHLCV)
    if df.empty:
        return 0
    rows = [
        (symbol, idx.strftime("%Y-%m-%d"), float(row["Open"]), float(row["High"]),
         float(row["Low"]), float(row["Close"]), float(row["Volume"]))
        for idx, row in df.iterrows()
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars "
            "(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)
