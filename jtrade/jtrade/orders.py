"""Build ib_async Order objects from queue entries — construction only, no transmit.

A Buy becomes a **bracket**: a parent LMT/MKT plus an attached protective STP
(``parentId`` linked, parent ``transmit=False`` so both go as one unit). A closing Sell
is a single LMT/MKT. Reference-only shorts build nothing. Order-id / parentId assignment
needs a connected client (``ib.client.getReqId()``); the actual ``placeOrder`` call lives
solely in execute.py, preserving the single guarded transmit path.
"""

from __future__ import annotations

from dataclasses import dataclass

from ib_async import Contract, LimitOrder, MarketOrder, Order, StopOrder


@dataclass
class BuiltOrder:
    contract: Contract
    orders: list[Order]     # [parent, stop] for a bracket, or [single] for a closing sell
    kind: str               # "bracket" | "single" | "none"
    note: str = ""


def _parent(action: str, qty: int, order_type: str, limit_price: float | None) -> Order:
    if order_type == "MKT" or limit_price is None:
        return MarketOrder(action, qty)
    return LimitOrder(action, qty, limit_price)


def build(entry: dict, contract: Contract, ib_client=None) -> BuiltOrder:
    """Build (but do not place) the ib_async orders for one queue entry."""
    action = entry["action"]
    qty = int(entry.get("quantity", 0))
    order_type = entry.get("order_type", "LMT")
    limit = entry.get("limit_price")
    stop = entry.get("stop_price")

    if entry.get("reference_only") or qty <= 0:
        return BuiltOrder(contract=contract, orders=[], kind="none",
                          note="reference-only / zero quantity — nothing to transmit")

    if action == "BUY" and stop:
        parent = _parent("BUY", qty, order_type, limit)
        child = StopOrder("SELL", qty, float(stop))
        # link into a single bracket unit when we have a connected client for ids
        if ib_client is not None and ib_client.is_connected():
            parent.orderId = ib_client.ib.client.getReqId()
            child.orderId = ib_client.ib.client.getReqId()
            child.parentId = parent.orderId
        parent.transmit = False   # parent held until child is submitted
        child.transmit = True     # submitting the child releases the whole bracket
        parent.ocaGroup = f"jtrade-{entry['id']}"
        return BuiltOrder(contract=contract, orders=[parent, child], kind="bracket",
                          note="parent + protective stop")

    # closing sell (flatten) or plain buy without a stop -> single order
    single = _parent(action, qty, order_type, limit)
    single.transmit = True
    return BuiltOrder(contract=contract, orders=[single], kind="single",
                      note="closing sell" if action == "SELL" else "single order")
