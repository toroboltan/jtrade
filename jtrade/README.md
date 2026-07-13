# jtrade

On-demand stock/ETF screening & trade-proposal agent, backed by a live Interactive
Brokers account (IB Gateway + `ib_async`). It screens the `etfsToMonitor.xlsx`
watchlist with a pluggable criteria engine (Weinstein Stage Analysis to start),
emits a per-run report, and **proposes** risk-managed orders for manual approval.
It never auto-executes: `scan → review → execute` are three separate human actions,
and execution runs behind a `DRY_RUN` guard during bring-up.

See `docs/` and the plan for the full design. Quickstart lives in
[docs/BRINGUP.md](docs/BRINGUP.md).
