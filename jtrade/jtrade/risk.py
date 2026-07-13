"""Risk engine: position sizing, stop placement, and portfolio-cap enforcement.

Turns Buy/Sell screen signals into concrete, risk-managed ``Proposal`` objects:

  Sizing    shares = floor(risk_frac · equity / (entry − stop)); skipped if ≤ 0.
  Stop      protective stop = ma_period MA · (1 − buffer); ATR fallback when the MA
            stop is invalid or further than max_stop_distance_pct from entry.
  Sells     flatten-first: size to close the held position; if flat, a short is emitted
            reference-only (flagged, never sized into caps or execution).
  Caps      enforced here AND re-checked at execute time:
              • ≤ max_new_proposals actionable proposals (drop lowest strength)
              • Σ(open + proposed risk) ≤ max_aggregate_risk · equity
              • post-trade cash ≥ min_cash_reserve
              • one proposal per symbol (de-dupe)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Settings
from .screen import ScreenResult, ScreenRow


@dataclass
class Proposal:
    ticker: str
    action: str                 # "BUY" or "SELL"
    quantity: int
    order_type: str             # "LMT" or "MKT"
    entry: float                # reference price used for sizing
    limit_price: float | None   # None for MKT
    stop_price: float | None    # protective stop (None for closing sells / reference short)
    risk_per_share: float
    risk_amount: float          # shares · risk_per_share (0 for closing sells)
    est_cost: float             # shares · entry (positive = cash out for buys)
    strength: float
    sheet: str
    stop_method: str = ""       # "ma_below" | "atr" | ""
    reference_only: bool = False
    rationale: str = ""
    dropped: bool = False
    drop_reason: str = ""


# ---- indicators ----
def atr(df: pd.DataFrame, period: int) -> float | None:
    if df is None or len(df) < period + 1:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return None if pd.isna(val) else float(val)


def moving_average(df: pd.DataFrame, period: int) -> float | None:
    if df is None or len(df) < period:
        return None
    val = df["Close"].rolling(period).mean().iloc[-1]
    return None if pd.isna(val) else float(val)


# ---- sizing & stops ----
def size_position(equity: float, entry: float, stop: float, risk_frac: float) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0 or equity <= 0:
        return 0
    return int(math.floor(risk_frac * equity / risk_per_share))


def compute_stop(df: pd.DataFrame, entry: float, stop_conf: dict) -> tuple[float | None, str]:
    """Return (stop_price, method). Prefers MA-below; falls back to ATR."""
    ma_period = int(stop_conf.get("ma_period", 150))
    buffer = float(stop_conf.get("ma_buffer_pct", 0.02))
    max_dist = float(stop_conf.get("max_stop_distance_pct", 0.25))
    atr_period = int(stop_conf.get("atr_period", 14))
    atr_mult = float(stop_conf.get("atr_mult", 2.0))

    ma = moving_average(df, ma_period)
    if ma is not None:
        candidate = ma * (1 - buffer)
        if 0 < candidate < entry and (entry - candidate) / entry <= max_dist:
            return round(candidate, 4), "ma_below"

    a = atr(df, atr_period)
    if a is not None and a > 0:
        candidate = entry - atr_mult * a
        if candidate > 0:
            return round(candidate, 4), "atr"
    return None, ""


# ---- proposal construction ----
def _limit_price(action: str, entry: float, offset: float) -> float:
    # buy slightly above / sell slightly below the reference to improve fill odds
    px = entry * (1 + offset) if action == "BUY" else entry * (1 - offset)
    return round(px, 4)


def build_proposals(
    result: ScreenResult,
    equity: float,
    cash: float,
    positions: dict[str, float],
    settings: Settings,
    open_risk: float = 0.0,
) -> list[Proposal]:
    """Build capped, sized proposals from Buy/Sell signals. ``positions`` maps
    UPPER symbol -> signed held qty. ``open_risk`` is the $ risk of existing bracketed
    positions (0 if unknown)."""
    rconf = settings.risk
    risk_frac = float(rconf["equity_risk_per_trade"])
    stop_conf = rconf.get("stop", {})
    offset = float(rconf.get("limit_offset_pct", 0.001))
    order_type = rconf.get("default_order_type", "LMT")
    max_new = int(rconf["max_new_proposals"])
    max_risk = float(rconf["max_aggregate_risk"]) * equity
    min_cash = float(rconf["min_cash_reserve"])

    raw: list[Proposal] = []
    # de-dupe by symbol; only tradable symbols become actionable proposals
    for row in result.dedup_rows():
        if row.signal not in ("Buy", "Sell"):
            continue
        if not row.tradable:
            continue
        if row.price is None or row.price <= 0:
            continue
        if row.signal == "Buy":
            raw.append(_build_buy(row, equity, offset, order_type, risk_frac, stop_conf))
        else:
            raw.append(_build_sell(row, positions.get(row.ticker.upper(), 0.0), offset, order_type))

    # drop zero-quantity buys (sizing failed / risk_per_share <= 0)
    proposals = [p for p in raw if p.reference_only or p.quantity > 0]

    actionable = [p for p in proposals if not p.reference_only]
    reference = [p for p in proposals if p.reference_only]

    # rank by strength (desc) for all cap decisions
    actionable.sort(key=lambda p: p.strength, reverse=True)

    # (1) cap number of new proposals
    for p in actionable[max_new:]:
        p.dropped = True
        p.drop_reason = f"exceeds max_new_proposals ({max_new})"
    kept = [p for p in actionable if not p.dropped]

    # (2) aggregate risk cap: Σ(open + proposed buy risk) ≤ max_risk
    running = open_risk
    for p in kept:
        add = p.risk_amount if p.action == "BUY" else 0.0
        if running + add > max_risk:
            p.dropped = True
            p.drop_reason = f"aggregate risk > {max_risk:,.0f} ({rconf['max_aggregate_risk']:.0%} equity)"
        else:
            running += add
    kept = [p for p in kept if not p.dropped]

    # (3) cash reserve: cash − Σ(buy cost) + Σ(sell proceeds) ≥ min_cash
    proceeds = sum(p.est_cost for p in kept if p.action == "SELL")
    available = cash + proceeds
    spend = 0.0
    for p in kept:
        if p.action != "BUY":
            continue
        if available - (spend + p.est_cost) < min_cash:
            p.dropped = True
            p.drop_reason = f"would breach min cash reserve (${min_cash:,.0f})"
        else:
            spend += p.est_cost
    # keep dropped flags on the objects; return everything (dropped + kept + reference)
    return actionable + reference


def _build_buy(row: ScreenRow, equity, offset, order_type, risk_frac, stop_conf) -> Proposal:
    entry = float(row.price)
    stop, method = compute_stop(row.bars, entry, stop_conf)
    if stop is None:
        return Proposal(
            ticker=row.ticker, action="BUY", quantity=0, order_type=order_type,
            entry=entry, limit_price=None, stop_price=None, risk_per_share=0.0,
            risk_amount=0.0, est_cost=0.0, strength=row.strength, sheet=row.sheet,
            dropped=True, drop_reason="no valid stop (insufficient history)",
            rationale="Stage 1→2 Buy",
        )
    qty = size_position(equity, entry, stop, risk_frac)
    rps = entry - stop
    limit = _limit_price("BUY", entry, offset) if order_type == "LMT" else None
    return Proposal(
        ticker=row.ticker, action="BUY", quantity=qty, order_type=order_type,
        entry=round(entry, 4), limit_price=limit, stop_price=stop,
        risk_per_share=round(rps, 4), risk_amount=round(qty * rps, 2),
        est_cost=round(qty * entry, 2), strength=row.strength, sheet=row.sheet,
        stop_method=method, rationale="Stage 1→2 Buy — bracket (parent + protective stop)",
    )


def _build_sell(row: ScreenRow, held: float, offset, order_type) -> Proposal:
    entry = float(row.price)
    limit = _limit_price("SELL", entry, offset) if order_type == "LMT" else None
    if held and held > 0:
        # flatten-first: size to close the held long
        qty = int(math.floor(held))
        return Proposal(
            ticker=row.ticker, action="SELL", quantity=qty, order_type=order_type,
            entry=round(entry, 4), limit_price=limit, stop_price=None,
            risk_per_share=0.0, risk_amount=0.0, est_cost=round(qty * entry, 2),
            strength=row.strength, sheet=row.sheet,
            rationale=f"Stage 3→4 Sell — flatten held position ({qty} sh)",
        )
    # no position: reference-only short (never executed in v1)
    return Proposal(
        ticker=row.ticker, action="SELL", quantity=0, order_type=order_type,
        entry=round(entry, 4), limit_price=limit, stop_price=None,
        risk_per_share=0.0, risk_amount=0.0, est_cost=0.0,
        strength=row.strength, sheet=row.sheet, reference_only=True,
        rationale="Stage 3→4 Sell — no position held; short shown for reference only",
    )
