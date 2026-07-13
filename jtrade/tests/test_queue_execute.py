"""Queue round-trip + guarded execute (dry-run never transmits)."""

from __future__ import annotations

import pandas as pd

from jtrade import execute, queue, risk
from jtrade.config import load_settings
from jtrade.screen import ScreenResult, ScreenRow
from tests.conftest import make_bars


def _make_queue(tmp_path):
    """Build a settings pointed at a temp state dir, write a small queue, return settings."""
    s = load_settings()
    # redirect state files into tmp so tests don't touch real state
    s.raw["execute"]["pending_orders"] = str(tmp_path / "pending_orders.yaml")
    s.raw["execute"]["audit_log"] = str(tmp_path / "audit.log")

    df = make_bars("AAPL", start=150, end=200, n=300, seed=2, noise=0.3)
    rows = [ScreenRow("s", "AAPL", 200.0, 1.0, 2, "Buy", 0.9, True, df)]
    res = ScreenResult(rows=rows, by_sheet={}, generated_at=pd.Timestamp.now())
    props = risk.build_proposals(res, 100_000, 50_000, {}, s)
    queue.write_queue(props, s, meta={"equity": 100_000, "cash": 50_000})
    return s


def test_queue_status_transitions(tmp_path):
    s = _make_queue(tmp_path)
    pending = queue.list_orders(s, "pending")
    assert pending, "expected at least one pending order"
    oid = pending[0]["id"]

    queue.approve(s, oid)
    assert queue.list_orders(s, "approved")[0]["id"] == oid

    queue.reject(s, oid)
    assert queue.list_orders(s, "rejected")[0]["id"] == oid


def test_execute_dry_run_transmits_nothing(tmp_path):
    s = _make_queue(tmp_path)
    oid = queue.list_orders(s, "pending")[0]["id"]
    queue.approve(s, oid)

    rep = execute.execute_approved(s, ib_client=None, dry_run=True)
    assert rep.dry_run is True
    assert rep.transmitted == []          # nothing transmitted
    assert any(sk["id"] == oid for sk in rep.skipped)
    # audit log written
    assert (tmp_path / "audit.log").exists()
    # order stays approved (not filled) in a dry run
    assert queue.list_orders(s, "approved")[0]["id"] == oid


def test_only_approved_are_acted_on(tmp_path):
    s = _make_queue(tmp_path)
    # nothing approved yet
    rep = execute.execute_approved(s, ib_client=None, dry_run=True)
    assert rep.transmitted == [] and rep.skipped == []
