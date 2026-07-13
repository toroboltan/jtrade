"""Run the active criteria over the universe and produce the results table.

Output rows carry everything the report and the risk engine need:
  sheet, TKT, Price, Change, Stage, Signal, strength, tradable, plus the raw daily
  bars (kept out of the serialized table) so the risk engine can size without refetching.

The screen is source-agnostic: it pulls bars via ``BarProvider`` (IBKR primary, yfinance
fallback, cache) and prices via the optional ``IBKRClient`` snapshot. When no live client
is present, Price/Change fall back to the last cached/historical close.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Settings
from .criteria.base import Criterion
from .criteria.registry import load_enabled
from .data.bars import BarProvider
from .universe import Universe, load_universe


@dataclass
class ScreenRow:
    sheet: str
    ticker: str
    price: float | None
    change_pct: float | None
    stage: int | None
    signal: str
    strength: float
    tradable: bool
    bars: pd.DataFrame = field(default=None, repr=False)  # raw daily OHLCV for sizing
    error: str | None = None


@dataclass
class ScreenResult:
    rows: list[ScreenRow]
    by_sheet: dict[str, list[ScreenRow]]
    generated_at: pd.Timestamp

    def signals(self, kind: str) -> list[ScreenRow]:
        return [r for r in self.dedup_rows() if r.signal == kind]

    def dedup_rows(self) -> list[ScreenRow]:
        """One row per ticker (first sheet wins) — used for proposal generation."""
        seen: dict[str, ScreenRow] = {}
        for r in self.rows:
            if r.ticker not in seen:
                seen[r.ticker] = r
        return list(seen.values())


def _benchmark_series(provider: BarProvider, symbol: str) -> pd.Series | None:
    df = provider.get_daily_bars(symbol)
    return df["Close"] if not df.empty else None


def run_screen(
    settings: Settings,
    provider: BarProvider,
    ib_client=None,
    universe: Universe | None = None,
) -> ScreenResult:
    universe = universe or load_universe(settings)
    criteria: list[Criterion] = load_enabled(settings)

    # Provide benchmark close to any criterion that uses relative strength.
    bench_sym = settings.criteria.get("stage_analysis", {}).get("benchmark", "SPY")
    bench = _benchmark_series(provider, bench_sym)
    for crit in criteria:
        crit.set_benchmark(bench)

    # Compute each unique symbol once, then fan out to its sheet rows.
    computed: dict[str, dict] = {}
    bars_cache: dict[str, pd.DataFrame] = {}
    for ticker in universe.all_tickers():
        df = provider.get_daily_bars(ticker)
        bars_cache[ticker] = df
        if df.empty:
            computed[ticker] = {"stage": None, "signal": "Hold", "strength": 0.0,
                                "error": "no data"}
            continue
        # combine criteria: first enabled drives Stage/Signal; strength = max across
        agg = {"stage": None, "signal": "Hold", "strength": 0.0, "error": None}
        for i, crit in enumerate(criteria):
            try:
                latest = crit.latest(df)
            except Exception as e:
                agg["error"] = f"{crit.name}: {type(e).__name__}: {e}"
                continue
            if i == 0:
                agg["stage"] = latest["Stage"]
                agg["signal"] = latest["Signal"]
            agg["strength"] = max(agg["strength"], latest["strength"])
        computed[ticker] = agg

    # Prices: live snapshot if connected, else last close from bars.
    def price_change(ticker: str) -> tuple[float | None, float | None]:
        if ib_client is not None and ib_client.is_connected():
            snap = ib_client.snapshot(ticker)
            if snap["price"] is not None:
                return snap["price"], snap["change_pct"]
        df = bars_cache.get(ticker)
        if df is not None and len(df) >= 2:
            last, prev = df["Close"].iloc[-1], df["Close"].iloc[-2]
            chg = (last - prev) / prev * 100.0 if prev else None
            return float(last), chg
        if df is not None and len(df) == 1:
            return float(df["Close"].iloc[-1]), None
        return None, None

    rows: list[ScreenRow] = []
    by_sheet: dict[str, list[ScreenRow]] = {}
    for sheet, tickers in universe.by_sheet.items():
        sheet_rows: list[ScreenRow] = []
        for ticker in tickers:
            c = computed.get(ticker, {})
            price, change = price_change(ticker)
            row = ScreenRow(
                sheet=sheet,
                ticker=ticker,
                price=price,
                change_pct=change,
                stage=c.get("stage"),
                signal=c.get("signal", "Hold"),
                strength=float(c.get("strength", 0.0) or 0.0),
                tradable=universe.symbols[ticker].tradable,
                bars=bars_cache.get(ticker),
                error=c.get("error"),
            )
            sheet_rows.append(row)
            rows.append(row)
        by_sheet[sheet] = sheet_rows

    return ScreenResult(rows=rows, by_sheet=by_sheet, generated_at=pd.Timestamp.now())
