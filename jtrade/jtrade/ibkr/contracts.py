"""Symbol -> IBKR Contract resolution.

For v1 we only propose orders on plain US Stocks/ETFs, so we map symbols to
``Stock(symbol, 'SMART', 'USD')``. ``resolve`` returns None for symbols we don't
attempt to trade (callers exclude these from proposals). Actual tradability is
confirmed separately by ``IBKRClient.qualify`` against the live contract database.
"""

from __future__ import annotations

from ib_async import Contract, Stock

# Symbols that are conceptually non-Stock/ETF even if they slip past config
# (indices, forex pairs, spot crypto). Kept as a defensive backstop; the
# primary monitor-only classification lives in universe.py / settings.yaml.
NON_EQUITY = {"VIX", "SPX", "NDX", "RUT", "DJI"}


def resolve(symbol: str) -> Contract | None:
    """Return a US Stock/ETF contract for ``symbol``, or None if not tradable here."""
    sym = symbol.strip().upper()
    if not sym or sym in NON_EQUITY:
        return None
    # SMART routing, USD-denominated US listing.
    return Stock(sym, "SMART", "USD")
