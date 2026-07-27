"""jtrade MCP server — the conversational front-end over jtrade.core.

Exposes the same scan/review/execute flow as the CLI as typed MCP tools, so an agent can
drive the whole loop in conversation. Execution stays guarded: `execute_approved` honors
DRY_RUN and only ever acts on rows the user has approved.

Run: ``uv run python mcp_server.py`` (registered in .mcp.json).
"""

from __future__ import annotations

import asyncio
from functools import partial

from mcp.server.fastmcp import FastMCP

from jtrade import core

mcp = FastMCP("jtrade")
FRONT_END = "mcp"


async def _offload(fn, *args, **kwargs):
    """Run a blocking core.* call in a worker thread.

    FastMCP invokes tool bodies inside its own running asyncio loop, but the
    synchronous ib_async API drives an event loop to completion (getLoop() ->
    the *running* loop -> ``RuntimeError: This event loop is already running``).
    Offloading to a worker thread gives ib_async a fresh loop off the server
    thread and keeps the FastMCP loop responsive during long scans.
    """
    return await asyncio.to_thread(partial(fn, *args, **kwargs))


@mcp.tool()
async def check_connection() -> dict:
    """Verify the IB Gateway connection and return account equity/cash."""
    return await _offload(core.check_connection, front_end=FRONT_END)


@mcp.tool()
async def check_subscriptions() -> dict:
    """Probe market-data entitlements; advise whether delayed mode is required."""
    return await _offload(core.check_subscriptions, front_end=FRONT_END)


@mcp.tool()
async def run_scan(offline: bool = False) -> dict:
    """Screen the whole watchlist, render the report, and fill the pending-orders queue.
    Set offline=true to run without IBKR (no order sizing)."""
    return await _offload(core.run_scan, front_end=FRONT_END, offline=offline)


@mcp.tool()
async def get_report(fmt: str = "markdown") -> str:
    """Return the latest scan report text ('markdown' or 'html')."""
    from jtrade.config import load_settings
    s = load_settings()
    path = s.report_dir / ("latest.html" if fmt == "html" else "latest.md")
    return path.read_text(encoding="utf-8") if path.exists() else "(no report yet — run run_scan)"


@mcp.tool()
async def list_pending_orders(status: str | None = None) -> list[dict]:
    """List queued orders. status defaults to all; pass 'pending'/'approved'/etc. to filter."""
    return await _offload(core.list_pending, status=status)


@mcp.tool()
async def approve_order(order_id: str) -> dict:
    """Approve one queued order by id (e.g. 'BUY-AAPL')."""
    return await _offload(core.approve_order, order_id)


@mcp.tool()
async def reject_order(order_id: str) -> dict:
    """Reject one queued order by id."""
    return await _offload(core.reject_order, order_id)


@mcp.tool()
async def execute_approved(dry_run: bool = True) -> dict:
    """Transmit approved orders. dry_run=true (default) builds+logs without transmitting;
    dry_run=false transmits for real (requires a live connection and off-DRY_RUN)."""
    return await _offload(core.execute_approved, front_end=FRONT_END, dry_run=dry_run)


@mcp.tool()
async def get_account() -> dict:
    """Return connection + account equity/cash."""
    return await _offload(core.get_account, front_end=FRONT_END)


@mcp.tool()
async def get_positions() -> list[dict]:
    """Return current IBKR positions (symbol, signed qty, avg cost)."""
    return await _offload(core.get_positions, front_end=FRONT_END)


if __name__ == "__main__":
    mcp.run()
