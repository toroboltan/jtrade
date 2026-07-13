# jtrade — bring-up guide

`jtrade` screens your `etfsToMonitor.xlsx` watchlist against Weinstein Stage Analysis,
reports by sheet, and **proposes** risk-managed orders for manual approval. It never
auto-executes. Three separate human actions:

```
scan     screen universe -> report + pending_orders.yaml
review   approve / reject (conversationally OR by editing the queue file)
execute  transmit ONLY approved orders (DRY_RUN guard on by default)
```

> Live-account safety: `execute.dry_run: true` is the default. Orders are built and
> logged but never transmitted until you explicitly run `jtrade execute --live` (or
> `execute_approved(dry_run=false)` via MCP) against an off-DRY_RUN config.

## 1. IBKR account / API config

In **IB Gateway → Configure → Settings → API → Settings**:
- ✅ *Enable ActiveX and Socket Clients*
- Socket port: **4001** (live) — paper is 4002
- ❌ leave *Read-Only API* **off** (needed to transmit orders)
- Add your client host to *Trusted IPs*
- Confirm live trading permissions for US stocks/ETFs

## 2. IB Gateway on WSL2 (recommended: Gateway on the Windows host)

Run IB Gateway on Windows; connect from WSL2 over the host IP:
```bash
ip route | grep default          # -> the Windows host IP, e.g. 172.x.x.1
```
- Allow inbound 4001 through Windows Firewall for the WSL subnet
- Add that IP to Gateway *Trusted IPs*
- Set `ibkr.host` in `config/settings.yaml` to the host IP (not 127.0.0.1)

(Alternative: run Gateway inside WSL2 via WSLg and keep `host: 127.0.0.1`.)

## 3. Python env (uv)

```bash
cd jtrade
uv sync --extra fallback        # installs ib_async, pandas, mcp, jinja2, yfinance, ...
```

## 4. Register the MCP server

`.mcp.json` is already committed:
```json
{ "mcpServers": { "jtrade": { "command": "uv", "args": ["run","python","mcp_server.py"], "cwd": "." } } }
```

## 5. Verify connection & data

```bash
uv run jtrade check-conn            # account + equity/cash
uv run jtrade check-subscriptions   # live vs delayed market data; advises market_data_type
```
If you have no live subscription, set `ibkr.market_data_type: 3` (delayed) in settings.

## 6. First scan (safe — DRY_RUN on)

```bash
uv run jtrade scan                  # writes reports/latest.html + state/pending_orders.yaml
uv run jtrade review --list         # inspect proposals
uv run jtrade review --approve BUY-AAPL
uv run jtrade execute               # DRY RUN: builds + logs, transmits nothing
```

## Going live (guarded)

1. Test on **paper** first: set `ibkr.port: 4002`, run the full loop with `--live`,
   confirm a bracket appears in Gateway with the right size + attached stop.
2. On live: `scan` with `dry_run: true`, inspect `reports/`, `state/pending_orders.yaml`,
   and `state/audit.log`. Only when satisfied, approve a single order and run
   `uv run jtrade execute --live`.

## Adding a new criterion

Drop a file in `jtrade/criteria/` subclassing `Criterion` with a `NAME` constant and a
`compute(df) -> df` adding `Stage`/`Signal`/`strength`, then add its name to
`criteria.enabled` in `settings.yaml`. No engine changes needed.

## Configuration reference

All knobs live in `config/settings.yaml`: IB ports/host, `risk` (1% per trade, 15%
aggregate, 10 proposals/scan, $4,000 cash floor), stop method, limit offset, and the
monitor-only sheet/symbol lists. The `DRY_RUN` env var overrides `execute.dry_run`.
