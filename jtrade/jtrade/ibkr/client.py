"""ib_async wrapper around IB Gateway — the only place that talks to IBKR.

Uses ib_async's synchronous API (each call runs the event loop to completion), which
suits the on-demand `scan / review / execute` flow. Construct with a Settings object and
use as a context manager::

    with IBKRClient(settings, front_end="cli") as ib:
        bars = ib.daily_bars("AAPL")
        equity = ib.account_equity()

Nothing here retries indefinitely; pacing for bulk historical requests is handled by the
caller (data/bars.py) via throttle + cache.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
from ib_async import IB, util

from ..config import Settings
from . import contracts


@dataclass
class AccountSnapshot:
    net_liquidation: float
    total_cash: float
    account: str


@dataclass
class PositionRow:
    symbol: str
    position: float          # signed qty (negative = short)
    avg_cost: float


class IBKRClient:
    def __init__(self, settings: Settings, front_end: str = "cli"):
        self.settings = settings
        self.ib = IB()
        ib = settings.ibkr
        self.host = ib["host"]
        self.port = int(ib["port"])
        self.timeout = float(ib.get("connect_timeout", 15))
        self.market_data_type = int(ib.get("market_data_type", 1))
        self._account = ib.get("account") or ""
        self.client_id = int(
            ib["client_id_mcp"] if front_end == "mcp" else ib["client_id_cli"]
        )

    # ---- lifecycle ----
    def connect(self) -> "IBKRClient":
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
        # request data type up-front (3 = delayed when the account lacks a live subscription)
        self.ib.reqMarketDataType(self.market_data_type)
        if not self._account:
            accts = self.ib.managedAccounts()
            self._account = accts[0] if accts else ""
        return self

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def __enter__(self) -> "IBKRClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.disconnect()

    @property
    def account(self) -> str:
        return self._account

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    # ---- contracts ----
    def qualify(self, symbol: str):
        """Return a qualified Contract for a US Stock/ETF, or None if IBKR can't map it."""
        c = contracts.resolve(symbol)
        if c is None:
            return None
        qualified = self.ib.qualifyContracts(c)
        return qualified[0] if qualified else None

    # ---- historical bars ----
    def daily_bars(self, symbol: str, years: int | None = None) -> pd.DataFrame:
        """Daily OHLCV as a DataFrame indexed by date. Empty frame if unavailable."""
        years = years or int(self.settings.data.get("history_years", 5))
        contract = self.qualify(symbol)
        if contract is None:
            return pd.DataFrame()
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{years} Y",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        if not bars:
            return pd.DataFrame()
        df = util.df(bars)
        df = df.rename(
            columns={
                "date": "Date", "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume",
            }
        )
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        return df[["Open", "High", "Low", "Close", "Volume"]]

    # ---- snapshot price / change ----
    def snapshot(self, symbol: str) -> dict:
        """Return {'price': float|None, 'change_pct': float|None} from a market snapshot."""
        contract = self.qualify(symbol)
        if contract is None:
            return {"price": None, "change_pct": None}
        [ticker] = self.ib.reqTickers(contract)
        last = ticker.marketPrice()
        close = ticker.close  # prior close
        change_pct = None
        if last is not None and close not in (None, 0) and not _isnan(last) and not _isnan(close):
            change_pct = (last - close) / close * 100.0
        price = None if last is None or _isnan(last) else float(last)
        return {"price": price, "change_pct": change_pct}

    # ---- account & positions ----
    def account_snapshot(self) -> AccountSnapshot:
        rows = self.ib.accountSummary(self._account or "All")
        vals = {r.tag: r.value for r in rows if (not self._account or r.account == self._account)}
        nl = float(vals.get("NetLiquidation", 0) or 0)
        cash = float(vals.get("TotalCashValue", vals.get("AvailableFunds", 0)) or 0)
        return AccountSnapshot(net_liquidation=nl, total_cash=cash, account=self._account)

    def account_equity(self) -> float:
        return self.account_snapshot().net_liquidation

    def positions(self) -> list[PositionRow]:
        rows = []
        for p in self.ib.positions(self._account) if self._account else self.ib.positions():
            rows.append(
                PositionRow(
                    symbol=p.contract.symbol,
                    position=float(p.position),
                    avg_cost=float(p.avgCost),
                )
            )
        return rows

    def position_for(self, symbol: str) -> float:
        sym = symbol.upper()
        return sum(p.position for p in self.positions() if p.symbol.upper() == sym)

    # ---- subscription probe ----
    def check_subscriptions(self, probe_symbol: str = "SPY") -> dict:
        """Report whether live snapshots work or delayed mode (type 3) is required."""
        result = {"connected": self.is_connected(), "account": self._account,
                  "market_data_type": self.market_data_type}
        try:
            self.ib.reqMarketDataType(self.market_data_type)
            snap = self.snapshot(probe_symbol)
            result["live_snapshot_ok"] = snap["price"] is not None
            result["probe"] = {probe_symbol: snap}
            if snap["price"] is None and self.market_data_type == 1:
                # retry delayed to advise the user
                self.ib.reqMarketDataType(3)
                delayed = self.snapshot(probe_symbol)
                result["delayed_ok"] = delayed["price"] is not None
                result["recommendation"] = (
                    "no live data; set ibkr.market_data_type: 3 (delayed)"
                    if delayed["price"] is not None
                    else "no live or delayed data — check account market-data subscriptions"
                )
                self.ib.reqMarketDataType(self.market_data_type)
        except Exception as e:  # surfaced to the user, not fatal
            result["error"] = f"{type(e).__name__}: {e}"
        return result


def _isnan(x) -> bool:
    try:
        return x != x
    except Exception:
        return False
