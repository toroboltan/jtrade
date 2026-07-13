"""Read the watchlist workbook into a classified, de-duplicated universe.

Layout (confirmed from etfsToMonitor.xlsx): every sheet holds the ticker in column A
(header varies: TKT / Index / ETF), TKT_DATA/Price/Change in B-D. Data starts row 2.

Classification:
- A symbol is *tradable* (eligible for order proposals) only if it appears on at least
  one sheet that is NOT in ``monitor_only_sheets`` and it is not listed in
  ``monitor_only_symbols`` (indices like VIX, spot crypto like LTC, forex).
- Everything else is *monitor-only*: still screened + reported, never proposed.

The workbook is only ever read, never written (it may be open in Excel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from .config import Settings


@dataclass
class Symbol:
    ticker: str
    sheets: list[str] = field(default_factory=list)   # every sheet this symbol appears on
    tradable: bool = True
    reason: str = ""                                   # why monitor-only, if applicable


@dataclass
class Universe:
    symbols: dict[str, Symbol]                         # ticker -> Symbol (de-duped)
    by_sheet: dict[str, list[str]]                     # sheet -> [tickers in original order]

    @property
    def tradable(self) -> list[str]:
        return [t for t, s in self.symbols.items() if s.tradable]

    @property
    def monitor_only(self) -> list[str]:
        return [t for t, s in self.symbols.items() if not s.tradable]

    def all_tickers(self) -> list[str]:
        return list(self.symbols.keys())


def load_universe(settings: Settings) -> Universe:
    """Load + classify the universe from the configured workbook."""
    uconf = settings.universe
    wb_path: Path = settings.workbook_path
    ticker_col = int(uconf.get("ticker_column", 1))
    header_row = int(uconf.get("header_row", 1))
    monitor_sheets = {s.lower() for s in uconf.get("monitor_only_sheets", [])}
    monitor_symbols = {s.upper() for s in uconf.get("monitor_only_symbols", [])}

    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)
    try:
        by_sheet: dict[str, list[str]] = {}
        symbols: dict[str, Symbol] = {}

        for ws in wb.worksheets:
            sheet_name = ws.title
            sheet_is_monitor = sheet_name.lower() in monitor_sheets
            tickers: list[str] = []
            for row in ws.iter_rows(
                min_row=header_row + 1, min_col=ticker_col, max_col=ticker_col, values_only=True
            ):
                raw = row[0]
                if raw is None:
                    continue
                ticker = str(raw).strip().upper()
                if not ticker:
                    continue
                tickers.append(ticker)

                sym = symbols.get(ticker)
                if sym is None:
                    sym = Symbol(ticker=ticker)
                    symbols[ticker] = sym
                if sheet_name not in sym.sheets:
                    sym.sheets.append(sheet_name)

            by_sheet[sheet_name] = tickers

        # Second pass: classify each de-duped symbol.
        for ticker, sym in symbols.items():
            if ticker in monitor_symbols:
                sym.tradable = False
                sym.reason = "listed as monitor-only symbol (index/forex/spot-crypto)"
                continue
            # tradable if it appears on any non-monitor sheet
            tradable_sheets = [s for s in sym.sheets if s.lower() not in monitor_sheets]
            if not tradable_sheets:
                sym.tradable = False
                sym.reason = "only appears on monitor-only sheet(s)"

        return Universe(symbols=symbols, by_sheet=by_sheet)
    finally:
        wb.close()
