"""jtrade CLI — one of two front-ends over jtrade.core.

    jtrade check-conn                 # verify IB Gateway connection + account
    jtrade check-subscriptions        # probe market-data entitlements (live vs delayed)
    jtrade scan [--offline]           # screen universe -> report + pending_orders.yaml
    jtrade review [--list|--approve ID|--reject ID]
    jtrade execute [--live]           # transmit approved orders (DRY_RUN unless --live)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core
from .config import load_settings

FRONT_END = "cli"


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_check_conn(args) -> int:
    res = core.check_connection(front_end=FRONT_END)
    _print(res)
    return 0 if res.get("connected") else 1


def cmd_check_subscriptions(args) -> int:
    _print(core.check_subscriptions(front_end=FRONT_END))
    return 0


def cmd_scan(args) -> int:
    summary = core.run_scan(front_end=FRONT_END, offline=args.offline)
    _print(summary)
    print(f"\nReport: {summary['report_html']}", file=sys.stderr)
    print(f"Queue:  {summary['pending_orders']}", file=sys.stderr)
    return 0


def cmd_review(args) -> int:
    settings = load_settings()
    if args.approve:
        _print(core.approve_order(args.approve, settings))
    elif args.reject:
        _print(core.reject_order(args.reject, settings))
    else:
        status = None if args.all else "pending"
        orders = core.list_pending(settings, status=status)
        _print(orders)
    return 0


def cmd_execute(args) -> int:
    # --live flips DRY_RUN off; default is a guarded dry run
    dry = False if args.live else True
    rep = core.execute_approved(front_end=FRONT_END, dry_run=dry)
    _print(rep)
    if rep.get("dry_run"):
        print("\n[DRY RUN] no orders transmitted. Re-run with --live to transmit.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jtrade", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check-conn", help="verify IB Gateway connection").set_defaults(func=cmd_check_conn)
    sub.add_parser("check-subscriptions", help="probe market-data entitlements").set_defaults(func=cmd_check_subscriptions)

    sp = sub.add_parser("scan", help="screen universe -> report + queue")
    sp.add_argument("--offline", action="store_true",
                    help="run without IBKR (yfinance/cache only; no proposals sized)")
    sp.set_defaults(func=cmd_scan)

    rv = sub.add_parser("review", help="list/approve/reject queued orders")
    rv.add_argument("--list", dest="list_", action="store_true", help="list pending (default)")
    rv.add_argument("--all", action="store_true", help="list all statuses")
    rv.add_argument("--approve", metavar="ID", help="approve order by id")
    rv.add_argument("--reject", metavar="ID", help="reject order by id")
    rv.set_defaults(func=cmd_review)

    ex = sub.add_parser("execute", help="transmit approved orders (guarded)")
    ex.add_argument("--live", action="store_true", help="actually transmit (DRY_RUN off)")
    ex.set_defaults(func=cmd_execute)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
