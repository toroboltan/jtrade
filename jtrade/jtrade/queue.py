"""The pending_orders.yaml confirmation queue — the editable half of dual approval.

Each proposal becomes one entry keyed by a stable ``id`` (ACTION-TICKER). Status flows:
  pending → approved → filled   (or)   pending → rejected
Dropped-by-caps proposals are written as status ``dropped`` (with a reason) and
reference-only shorts as ``reference``; neither is executable. The user approves either
conversationally (MCP/CLI) or by editing this file directly — both paths land here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .risk import Proposal

APPROVABLE = "pending"
STATUSES = {"pending", "approved", "rejected", "filled", "dropped", "reference"}


def _order_id(p: Proposal) -> str:
    return f"{p.action}-{p.ticker}"


def _status_for(p: Proposal) -> str:
    if p.dropped:
        return "dropped"
    if p.reference_only:
        return "reference"
    return "pending"


def proposal_to_entry(p: Proposal) -> dict[str, Any]:
    return {
        "id": _order_id(p),
        "ticker": p.ticker,
        "action": p.action,
        "quantity": int(p.quantity),
        "order_type": p.order_type,
        "entry": p.entry,
        "limit_price": p.limit_price,
        "stop_price": p.stop_price,
        "risk_amount": p.risk_amount,
        "est_cost": p.est_cost,
        "strength": round(p.strength, 4),
        "sheet": p.sheet,
        "reference_only": p.reference_only,
        "status": _status_for(p),
        "rationale": p.rationale,
        "drop_reason": p.drop_reason or None,
    }


def write_queue(proposals: list[Proposal], settings: Settings, meta: dict | None = None) -> Path:
    path = settings.pending_orders_path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": meta or {},
        "orders": [proposal_to_entry(p) for p in proposals],
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    return path


def read_queue(settings: Settings) -> dict[str, Any]:
    path = settings.pending_orders_path
    if not path.exists():
        return {"meta": {}, "orders": []}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"meta": {}, "orders": []}


def _save(doc: dict, settings: Settings) -> None:
    with settings.pending_orders_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)


def list_orders(settings: Settings, status: str | None = None) -> list[dict]:
    orders = read_queue(settings).get("orders", [])
    return [o for o in orders if status is None or o.get("status") == status]


def set_status(settings: Settings, order_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"invalid status '{status}'; expected one of {sorted(STATUSES)}")
    doc = read_queue(settings)
    for o in doc.get("orders", []):
        if o.get("id") == order_id:
            # only pending orders can be approved/rejected via the API
            if status in ("approved", "rejected") and o.get("status") not in ("pending", "approved", "rejected"):
                raise ValueError(
                    f"order '{order_id}' is '{o.get('status')}' and cannot be {status}"
                )
            o["status"] = status
            _save(doc, settings)
            return o
    raise KeyError(f"order id '{order_id}' not found in queue")


def approve(settings: Settings, order_id: str) -> dict:
    return set_status(settings, order_id, "approved")


def reject(settings: Settings, order_id: str) -> dict:
    return set_status(settings, order_id, "rejected")
