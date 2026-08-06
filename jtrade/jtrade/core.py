"""Shared orchestration core — the single implementation behind both front-ends.

The CLI (cli.py) and the MCP server (mcp_server.py) are thin adapters over these
functions, so a scan/review/execute performed either way produces identical results.
Every IBKR connection is opened per-call and closed promptly (on-demand model).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from . import dashboard, execute as execute_mod
from . import queue, report, risk
from .config import Settings, load_settings
from .data.bars import BarProvider
from .ibkr.client import IBKRClient
from .screen import run_screen
from .universe import load_universe


@contextmanager
def _maybe_client(settings: Settings, front_end: str, require: bool):
    """Yield a connected IBKRClient, or None if connection fails and it isn't required."""
    client = IBKRClient(settings, front_end=front_end)
    try:
        client.connect()
        yield client
    except Exception:
        if require:
            raise
        yield None
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def check_connection(settings: Settings | None = None, front_end: str = "cli") -> dict:
    settings = settings or load_settings()
    try:
        with _maybe_client(settings, front_end, require=True) as ib:
            snap = ib.account_snapshot()
            return {"connected": True, "account": snap.account,
                    "net_liquidation": snap.net_liquidation, "cash": snap.total_cash,
                    "host": ib.host, "port": ib.port, "client_id": ib.client_id}
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {e}",
                "host": settings.ibkr["host"], "port": settings.ibkr["port"]}


def check_subscriptions(settings: Settings | None = None, front_end: str = "cli") -> dict:
    settings = settings or load_settings()
    try:
        with _maybe_client(settings, front_end, require=True) as ib:
            return ib.check_subscriptions()
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {e}"}


def run_scan(settings: Settings | None = None, front_end: str = "cli",
             offline: bool = False) -> dict:
    """Screen the universe, render the report, build+queue proposals. Returns a summary."""
    settings = settings or load_settings()
    universe = load_universe(settings)

    with _maybe_client(settings, front_end, require=not offline) as ib:
        connected = ib is not None
        provider = BarProvider(settings, ib_client=ib)
        result = run_screen(settings, provider, universe=universe)
        report_paths = report.render(result, settings)

        if connected:
            snap = ib.account_snapshot()
            equity, cash = snap.net_liquidation, snap.total_cash
            positions = {p.symbol.upper(): p.position for p in ib.positions()}
        else:
            equity, cash, positions = 0.0, 0.0, {}

        proposals = risk.build_proposals(result, equity, cash, positions, settings)
        queue_path = queue.write_queue(
            proposals, settings,
            meta={"generated_at": result.generated_at.isoformat(),
                  "equity": equity, "cash": cash, "offline": ib is None},
        )
        dash_paths = dashboard.render(result, proposals, equity, cash, settings)

    actionable = [p for p in proposals if not p.dropped and not p.reference_only]
    return {
        "generated_at": result.generated_at.isoformat(),
        "symbols_screened": len({r.ticker for r in result.rows}),
        "buy_signals": len(result.signals("Buy")),
        "sell_signals": len(result.signals("Sell")),
        "proposals_actionable": len(actionable),
        "proposals_dropped": len([p for p in proposals if p.dropped]),
        "reference_only": len([p for p in proposals if p.reference_only]),
        "equity": equity, "cash": cash, "offline": not connected,
        "report_markdown": str(report_paths["latest_md"]),
        "report_html": str(report_paths["latest_html"]),
        "dashboard_html": str(dash_paths["latest_dashboard"]),
        "pending_orders": str(queue_path),
    }


def list_pending(settings: Settings | None = None, status: str | None = None) -> list[dict]:
    settings = settings or load_settings()
    return queue.list_orders(settings, status=status)


def approve_order(order_id: str, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    return queue.approve(settings, order_id)


def reject_order(order_id: str, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    return queue.reject(settings, order_id)


def execute_approved(settings: Settings | None = None, front_end: str = "cli",
                     dry_run: bool | None = None) -> dict:
    settings = settings or load_settings()
    live = dry_run is False
    with _maybe_client(settings, front_end, require=live) as ib:
        rep = execute_mod.execute_approved(settings, ib_client=ib, dry_run=dry_run)
    return asdict(rep)


def get_account(settings: Settings | None = None, front_end: str = "cli") -> dict:
    return check_connection(settings, front_end)


def get_positions(settings: Settings | None = None, front_end: str = "cli") -> list[dict]:
    settings = settings or load_settings()
    with _maybe_client(settings, front_end, require=True) as ib:
        return [asdict(p) for p in ib.positions()]
