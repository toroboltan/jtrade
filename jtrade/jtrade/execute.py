"""The single guarded transmit path. Nothing else in jtrade calls placeOrder.

``execute_approved`` acts ONLY on queue rows with ``status: approved``. It:
  1. re-validates portfolio caps against a fresh account/position snapshot,
  2. honors DRY_RUN (default on): builds + logs every order but never transmits,
  3. writes an append-only audit log of every decision,
  4. on a real transmit, places the bracket/closing order and marks the row ``filled``.

Live transmit requires a connected client and DRY_RUN off; otherwise it is a dry run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import orders, queue
from .config import Settings


@dataclass
class ExecReport:
    dry_run: bool
    transmitted: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    rejected_by_caps: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    account: str = ""
    equity: float | None = None


def _audit(settings: Settings, event: dict) -> None:
    path = settings.audit_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def _recheck_caps(approved: list[dict], equity: float, cash: float, settings: Settings,
                  open_risk: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Return (allowed, rejected) after re-checking aggregate risk + cash at execute time."""
    rconf = settings.risk
    max_risk = float(rconf["max_aggregate_risk"]) * equity if equity else float("inf")
    min_cash = float(rconf["min_cash_reserve"])

    # sells first (they free cash and reduce risk), then buys ranked by strength
    sells = [o for o in approved if o["action"] == "SELL"]
    buys = sorted((o for o in approved if o["action"] == "BUY"),
                  key=lambda o: o.get("strength", 0), reverse=True)

    allowed, rejected = list(sells), []
    proceeds = sum(o.get("est_cost", 0) or 0 for o in sells)
    running_risk = open_risk
    spend = 0.0
    available = (cash or 0) + proceeds
    for o in buys:
        risk = o.get("risk_amount", 0) or 0
        cost = o.get("est_cost", 0) or 0
        if running_risk + risk > max_risk:
            rejected.append({**o, "reject_reason": f"aggregate risk > {max_risk:,.0f}"})
            continue
        if available - (spend + cost) < min_cash:
            rejected.append({**o, "reject_reason": f"cash reserve < {min_cash:,.0f}"})
            continue
        running_risk += risk
        spend += cost
        allowed.append(o)
    return allowed, rejected


def execute_approved(settings: Settings, ib_client=None, dry_run: bool | None = None) -> ExecReport:
    dry = settings.dry_run if dry_run is None else dry_run
    connected = ib_client is not None and ib_client.is_connected()
    rep = ExecReport(dry_run=dry or not connected)

    approved = queue.list_orders(settings, status="approved")
    approved = [o for o in approved if not o.get("reference_only") and int(o.get("quantity", 0)) > 0]

    # fresh snapshot for cap re-check (fall back to queue meta when offline/dry)
    equity, cash = None, None
    if connected:
        snap = ib_client.account_snapshot()
        equity, cash, rep.account = snap.net_liquidation, snap.total_cash, snap.account
    else:
        meta = queue.read_queue(settings).get("meta", {})
        equity, cash = meta.get("equity"), meta.get("cash")
    rep.equity = equity

    if equity:
        allowed, rejected = _recheck_caps(approved, equity, cash or 0, settings)
    else:
        allowed, rejected = approved, []  # cannot recheck without equity; log this
        _audit(settings, {"event": "caps_recheck_skipped", "reason": "no equity snapshot"})
    rep.rejected_by_caps = rejected
    for o in rejected:
        _audit(settings, {"event": "rejected_by_caps", "id": o["id"], "reason": o["reject_reason"]})

    for o in allowed:
        oid = o["id"]
        if dry or not connected:
            # build to validate shape, log, but do NOT transmit
            note = "DRY_RUN" if dry else "not connected"
            _audit(settings, {"event": "dry_run_build", "id": oid, "mode": note,
                              "action": o["action"], "qty": o["quantity"],
                              "limit": o.get("limit_price"), "stop": o.get("stop_price")})
            rep.skipped.append({"id": oid, "reason": note})
            continue
        try:
            contract = ib_client.qualify(o["ticker"])
            if contract is None:
                raise ValueError(f"could not qualify contract for {o['ticker']}")
            built = orders.build(o, contract, ib_client=ib_client)
            if built.kind == "none":
                rep.skipped.append({"id": oid, "reason": built.note})
                continue
            for order_obj in built.orders:
                ib_client.ib.placeOrder(contract, order_obj)   # <-- the only transmit call
            queue.set_status(settings, oid, "filled")
            rep.transmitted.append(oid)
            _audit(settings, {"event": "transmitted", "id": oid, "kind": built.kind,
                              "action": o["action"], "qty": o["quantity"]})
        except Exception as e:
            rep.errors.append({"id": oid, "error": f"{type(e).__name__}: {e}"})
            _audit(settings, {"event": "transmit_error", "id": oid, "error": str(e)})

    _audit(settings, {"event": "execute_summary", "dry_run": rep.dry_run,
                      "transmitted": rep.transmitted, "skipped": len(rep.skipped),
                      "rejected_by_caps": len(rep.rejected_by_caps), "errors": len(rep.errors)})
    return rep
