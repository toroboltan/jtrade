"""Render the scan as a self-contained, interactive HTML dashboard.

This is the richer sibling of ``report.py``: a trading-terminal readout that
surfaces the actionable Buy signals as spec cards (entry/limit/stop/risk pulled
from the sized proposals) above the full per-sheet universe, with client-side
filtering by signal, ticker, and **market stage**.

Unlike ``report.render`` it needs the sized ``proposals`` plus the account
equity/cash, so ``core.run_scan`` calls it after the queue is built. The output
is a single dependency-free file (inline CSS+JS, theme-aware) written to
``reports/latest_dashboard.html`` (+ a timestamped copy).
"""

from __future__ import annotations

import html
from pathlib import Path

import yaml

from .config import Settings
from .criteria.stage_analysis import STAGE_DESCRIPTIONS
from .indicators import MA_ROADMAP
from .report import _fmt_change, _fmt_price, _ma_class
from .risk import Proposal
from .screen import ScreenResult, ScreenRow

STAGE_SHORT = {s: d.split(" (")[0] for s, d in STAGE_DESCRIPTIONS.items()}

_MA_TH = "".join(
    f'<th class="num" title="{html.escape(desc)}">{html.escape(label)}</th>'
    for _key, label, _kind, _period, desc in MA_ROADMAP
)


# --------------------------------------------------------------------------- #
# data prep
# --------------------------------------------------------------------------- #
def _stage_counts(rows: list[ScreenRow]) -> dict[int, int]:
    c = {1: 0, 2: 0, 3: 0, 4: 0}
    for r in rows:
        if r.stage in c:
            c[r.stage] += 1
    return c


def _meter(counts: dict[int, int], total: int) -> str:
    total = total or 1
    out = []
    for s in (1, 2, 3, 4):
        pct = counts[s] / total * 100
        out.append(
            f'<span class="seg s{s}" style="width:{pct:.3f}%" '
            f'title="Stage {s} · {STAGE_SHORT[s]}: {counts[s]}"></span>'
        )
    return "".join(out)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _load_compass(settings: Settings) -> dict | None:
    path = settings.market_compass_file
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or None


_LEAN_ICON = {"bullish": "▲", "bearish": "▼", "neutral": "●"}
_SCENARIO_STAGE = {"Bull": "s2", "Base": "s3", "Bear": "s4"}


def _compass(data: dict) -> str:
    src = data.get("source", {})
    m = data.get("map", {})
    val = data.get("validation", {})
    concluded = val.get("concluded_scenario", "")

    scenario_cards = "".join(
        f"""
        <div class="compass__scenario{' is-lead' if sc.get('name') == concluded else ''}">
          <div class="compass__sc-head">
            <span class="compass__sc-name"><span class="dot {_SCENARIO_STAGE.get(sc.get('name'), 's1')}"></span>{html.escape(sc.get('name', ''))}</span>
            <span class="compass__sc-pct mono">{sc.get('probability', 0)}%</span>
          </div>
          <div class="compass__sc-bar"><span class="{_SCENARIO_STAGE.get(sc.get('name'), 's1')}" style="width:{sc.get('probability', 0)}%"></span></div>
          <p class="compass__sc-summary">{html.escape(str(sc.get('summary', '')).strip())}</p>
        </div>"""
        for sc in data.get("scenarios", [])
    )

    premise_items = "".join(
        f"""
        <li class="lean-{p.get('lean', 'neutral')}">
          <span class="compass__lean-icon">{_LEAN_ICON.get(p.get('lean', 'neutral'), '●')}</span>
          <div><b>{html.escape(p.get('label', ''))}</b><p>{html.escape(p.get('detail', ''))}</p></div>
        </li>"""
        for p in val.get("premises", [])
    )

    watch_rows = "".join(
        f"""<tr><td class="c-tkr"><span class="tkrname">{html.escape(w.get('ticker', ''))}</span></td>
            <td>{html.escape(w.get('thesis', ''))}</td>
            <td><span class="pill pill--watch">{html.escape(w.get('status', ''))}</span></td></tr>"""
        for w in data.get("watchlist", [])
    )

    catalysts = "".join(
        f"<li>{html.escape(c)}</li>" for c in val.get("next_catalysts", [])
    )

    src_line = f"{html.escape(src.get('title', ''))} — {html.escape(src.get('published', ''))}"
    src_url = src.get("url")
    if src_url:
        src_line = f'<a href="{html.escape(src_url)}" target="_blank" rel="noopener">{src_line}</a>'

    return f"""
      <section class="compass">
        <div class="section-head">
          <h2>Market Compass</h2>
          <p>{src_line} &nbsp;·&nbsp; validated {html.escape(val.get('as_of', ''))}</p>
        </div>
        <p class="compass__map mono">{m.get('breakdown_target', '—')} &larr; {m.get('range_low', '—')} — chop — {m.get('range_high', '—')} &rarr; {html.escape(str(m.get('breakout_target', '—')))}</p>
        <div class="compass__scenarios">{scenario_cards}</div>
        <p class="compass__conclusion"><b>Most probable now: {html.escape(concluded)}.</b> {html.escape(val.get('concluded_note', '').strip())}</p>
        <div class="compass__grid">
          <div class="compass__premises">
            <h3>Premise checklist</h3>
            <ul>{premise_items}</ul>
          </div>
          <div class="compass__side">
            <h3>Watchlist</h3>
            <table><tbody>{watch_rows}</tbody></table>
            <h3>Next catalysts</h3>
            <ul class="compass__catalysts">{catalysts}</ul>
          </div>
        </div>
      </section>"""


def _roadmap_legend() -> str:
    items = "".join(
        f'<div class="rm__item"><span class="rm__metric mono">{html.escape(label)}</span>'
        f'<span class="rm__desc">{html.escape(desc)}</span></div>'
        for _key, label, _kind, _period, desc in MA_ROADMAP
    )
    return f"""
      <section class="mix">
        <div class="mix__head">
          <span class="mix__title">Moving Average Road Map</span>
          <span class="mix__total">% distance from price</span>
        </div>
        <div class="rm">{items}</div>
      </section>"""


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #
def _signal_card(p: Proposal, equity: float) -> str:
    entry, stop, limit = p.entry, p.stop_price or 0.0, p.limit_price
    stop_dist = (entry - stop) / entry * 100 if entry and stop else 0.0
    risk_pct = p.risk_amount / equity * 100 if equity else 0.0
    cost_pct = p.est_cost / equity * 100 if equity else 0.0
    limit_str = f"{limit:,.4f}" if limit is not None else "MKT"
    return f"""
      <article class="signal">
        <header class="signal__head">
          <div class="signal__id">
            <span class="tkr">{html.escape(p.ticker)}</span>
            <span class="signal__sheet">{html.escape(p.sheet)}</span>
          </div>
          <span class="pill pill--buy">BUY</span>
        </header>
        <div class="signal__stage"><span class="dot s2"></span>Stage&nbsp;1&nbsp;→&nbsp;2 breakout · entering Markup</div>
        <dl class="spec">
          <div><dt>Entry ref</dt><dd class="mono">{entry:,.2f}</dd></div>
          <div><dt>Limit</dt><dd class="mono">{limit_str}</dd></div>
          <div><dt>Stop</dt><dd class="mono">{stop:,.2f}<span class="sub down">−{stop_dist:.1f}%</span></dd></div>
          <div><dt>Shares</dt><dd class="mono">{p.quantity}</dd></div>
          <div><dt>Risk</dt><dd class="mono">{_money(p.risk_amount)}<span class="sub">{risk_pct:.2f}% eq</span></dd></div>
          <div><dt>Est. cost</dt><dd class="mono">{_money(p.est_cost)}<span class="sub">{cost_pct:.1f}% eq</span></dd></div>
        </dl>
        <div class="strength">
          <span class="strength__label">Signal strength</span>
          <span class="strength__track"><span class="strength__fill" style="width:{p.strength * 100:.1f}%"></span></span>
          <span class="strength__val mono">{p.strength:.2f}</span>
        </div>
        <p class="signal__note">Bracket order — parent limit buy plus an attached protective stop {stop_dist:.1f}% below entry ({html.escape(p.stop_method or 'stop')}).</p>
      </article>"""


def _row(r: ScreenRow) -> str:
    monitor = not r.tradable
    cls = []
    if r.signal == "Buy":
        cls.append("is-buy")
    elif r.signal == "Sell":
        cls.append("is-sell")
    if monitor:
        cls.append("is-mon")

    price = _fmt_price(r.price)
    change = _fmt_change(r.change_pct)
    chg_cls = ""
    if change != "—":
        chg_cls = " up" if (r.change_pct or 0) >= 0 else " down"

    if r.stage:
        stage_cell = (
            f'<span class="stage s{r.stage}"><span class="dot s{r.stage}"></span>'
            f'{r.stage}<span class="stage__name"> · {STAGE_SHORT[r.stage]}</span></span>'
        )
    else:
        stage_cell = '<span class="stage stage--na">—</span>'

    if r.signal == "Buy":
        sig = '<span class="pill pill--buy">Buy</span>'
    elif r.signal == "Sell":
        sig = '<span class="pill pill--sell">Sell</span>'
    else:
        sig = '<span class="hold">Hold</span>'

    mon = ('<span class="montag" title="monitor-only — no order proposals">M</span>'
           if monitor else "")
    tkr = html.escape(r.ticker)
    ma_cells = "".join(
        f'<td class="num mono{_ma_class(r.ma_diffs.get(key))}">{_fmt_change(r.ma_diffs.get(key))}</td>'
        for key, *_rest in MA_ROADMAP
    )
    return (
        f'<tr class="{" ".join(cls)}" data-signal="{r.signal}" '
        f'data-stage="{r.stage or 0}" data-mon="{int(monitor)}" data-tkr="{tkr}">'
        f'<td class="c-tkr"><span class="tkrname">{tkr}</span>{mon}</td>'
        f'<td class="num mono">{price}</td>'
        f'<td class="num mono{chg_cls}">{change}</td>'
        f'<td class="c-stage">{stage_cell}</td>'
        f'<td class="c-sig">{sig}</td>{ma_cells}</tr>'
    )


def _panel(name: str, rows: list[ScreenRow]) -> str:
    counts = _stage_counts(rows)
    nsig = sum(1 for r in rows if r.signal in ("Buy", "Sell"))
    sig_badge = (f'<span class="panel__sig">{nsig} signal{"s" if nsig != 1 else ""}</span>'
                 if nsig else "")
    body = "\n".join(_row(r) for r in rows)
    esc = html.escape(name)
    return f"""
      <section class="panel" data-sheet="{esc}">
        <div class="panel__head">
          <h3>{esc}</h3>
          <div class="panel__meta">
            {sig_badge}
            <span class="panel__count mono">{len(rows)}</span>
            <span class="panel__meter">{_meter(counts, sum(counts.values()))}</span>
          </div>
        </div>
        <div class="tablewrap">
          <table>
            <thead><tr><th>Ticker</th><th class="num">Price</th><th class="num">Chg</th><th>Stage</th><th>Signal</th>{_MA_TH}</tr></thead>
            <tbody>
{body}
            </tbody>
          </table>
        </div>
      </section>"""


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def render(result: ScreenResult, proposals: list[Proposal], equity: float,
           cash: float, settings: Settings) -> dict[str, Path]:
    all_rows = result.rows
    gcounts = _stage_counts(all_rows)
    gtotal = sum(gcounts.values())

    n_symbols = len({r.ticker for r in all_rows})
    n_sheets = len(result.by_sheet)
    n_buy = len(result.signals("Buy"))
    n_sell = len(result.signals("Sell"))

    buys = [p for p in proposals
            if p.action == "BUY" and not p.dropped and not p.reference_only and p.quantity > 0]
    if buys:
        cards = "".join(_signal_card(p, equity) for p in buys)
    else:
        cards = ('<article class="signal signal--empty"><p>No actionable '
                 'Stage&nbsp;1&nbsp;→&nbsp;2 signals in this scan. The universe is '
                 'screened and reported below.</p></article>')

    panels = "".join(_panel(name, rows) for name, rows in result.by_sheet.items())

    compass_data = _load_compass(settings)
    compass_html = _compass(compass_data) if compass_data else ""

    date_part = result.generated_at.strftime("%Y-%m-%d")
    time_part = result.generated_at.strftime("%H:%M:%S")

    page = (_PAGE
            .replace("{{CSS}}", _CSS)
            .replace("{{JS}}", _JS)
            .replace("{{DATE}}", date_part)
            .replace("{{TIME}}", time_part)
            .replace("{{EQUITY}}", _money(equity))
            .replace("{{CASH}}", _money(cash))
            .replace("{{N_SYMBOLS}}", str(n_symbols))
            .replace("{{N_SHEETS}}", str(n_sheets))
            .replace("{{N_BUY}}", str(n_buy))
            .replace("{{N_SELL}}", str(n_sell))
            .replace("{{G_TOTAL}}", str(gtotal))
            .replace("{{G1}}", str(gcounts[1]))
            .replace("{{G2}}", str(gcounts[2]))
            .replace("{{G3}}", str(gcounts[3]))
            .replace("{{G4}}", str(gcounts[4]))
            .replace("{{METER}}", _meter(gcounts, gtotal))
            .replace("{{COMPASS}}", compass_html)
            .replace("{{ROADMAP}}", _roadmap_legend())
            .replace("{{CARDS}}", cards)
            .replace("{{PANELS}}", panels))

    out_dir: Path = settings.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.generated_at.strftime("%Y%m%d_%H%M%S")
    paths = {
        "dashboard": out_dir / f"scan_{stamp}_dashboard.html",
        "latest_dashboard": out_dir / "latest_dashboard.html",
    }
    for p in paths.values():
        p.write_text(page, encoding="utf-8")
    return paths


# --------------------------------------------------------------------------- #
# template  (single %-free string; {{TOKENS}} substituted above)
# --------------------------------------------------------------------------- #
_PAGE = """<title>jtrade scan — {{DATE}}</title>
<style>
{{CSS}}
</style>

<header class="topbar">
  <div class="brand">
    <span class="brand__mark mono">jtrade</span>
    <span class="brand__eyebrow">Stage&nbsp;Scan</span>
  </div>
  <div class="topbar__right">
    <div class="acct">
      <span class="acct__chip"><span class="acct__k">Net&nbsp;Liq</span><span class="acct__v mono">{{EQUITY}}</span></span>
      <span class="acct__chip"><span class="acct__k">Cash</span><span class="acct__v mono">{{CASH}}</span></span>
    </div>
    <button id="theme" class="theme" type="button" aria-label="Toggle light or dark theme">
      <span class="theme__dot"></span><span class="theme__txt">Theme</span>
    </button>
  </div>
</header>

<main>
  <section class="hero">
    <div class="hero__lead">
      <p class="eyebrow">Weinstein stage analysis · live IBKR data</p>
      <h1>Market scan</h1>
      <p class="hero__sub">{{DATE}} · {{TIME}} &nbsp;·&nbsp; {{N_SYMBOLS}} symbols across {{N_SHEETS}} watchlist sheets, each tagged by market stage and screened for stage-transition entries.</p>
    </div>
    <div class="kpis">
      <div class="kpi"><span class="kpi__n mono">{{N_SYMBOLS}}</span><span class="kpi__l">Symbols</span></div>
      <div class="kpi"><span class="kpi__n mono">{{N_SHEETS}}</span><span class="kpi__l">Sheets</span></div>
      <div class="kpi kpi--buy"><span class="kpi__n mono">{{N_BUY}}</span><span class="kpi__l">Buy signals</span></div>
      <div class="kpi kpi--sell"><span class="kpi__n mono">{{N_SELL}}</span><span class="kpi__l">Sell signals</span></div>
    </div>
  </section>

  {{COMPASS}}

  <section class="mix">
    <div class="mix__head">
      <span class="mix__title">Universe by stage</span>
      <span class="mix__total mono">{{G_TOTAL}} readings</span>
    </div>
    <div class="meter meter--lg">{{METER}}</div>
    <ul class="legend">
      <li><span class="dot s1"></span>1 · Accumulation <b class="mono">{{G1}}</b></li>
      <li><span class="dot s2"></span>2 · Markup <b class="mono">{{G2}}</b></li>
      <li><span class="dot s3"></span>3 · Distribution <b class="mono">{{G3}}</b></li>
      <li><span class="dot s4"></span>4 · Decline <b class="mono">{{G4}}</b></li>
    </ul>
  </section>

  {{ROADMAP}}

  <section class="signals">
    <div class="section-head">
      <h2>Actionable signals</h2>
      <p>Stage&nbsp;1&nbsp;→&nbsp;2 breakouts, risk-sized and bracketed. Awaiting review — nothing transmitted.</p>
    </div>
    <div class="signals__grid">
      {{CARDS}}
    </div>
  </section>

  <section class="universe">
    <div class="controls">
      <div class="seg-toggle" role="group" aria-label="Filter rows by signal">
        <button class="seg-btn is-active" data-mode="all" type="button">All rows</button>
        <button class="seg-btn" data-mode="signals" type="button">Signals only</button>
      </div>
      <div class="stage-filter" role="group" aria-label="Filter by market stage">
        <span class="stage-filter__lbl">Stage</span>
        <button class="stage-chip is-on s1" data-stage="1" type="button" aria-pressed="true"><span class="dot s1"></span>1</button>
        <button class="stage-chip is-on s2" data-stage="2" type="button" aria-pressed="true"><span class="dot s2"></span>2</button>
        <button class="stage-chip is-on s3" data-stage="3" type="button" aria-pressed="true"><span class="dot s3"></span>3</button>
        <button class="stage-chip is-on s4" data-stage="4" type="button" aria-pressed="true"><span class="dot s4"></span>4</button>
      </div>
      <label class="search">
        <input id="q" type="search" placeholder="Filter ticker…" autocomplete="off" spellcheck="false">
      </label>
    </div>
    <div id="panels" class="panels">
      {{PANELS}}
    </div>
    <p id="empty" class="empty" hidden>No rows match the current filter.</p>
  </section>

  <footer class="foot">
    <p><span class="montag">M</span> monitor-only — screened &amp; reported, never proposed (indices, forex, spot crypto, or symbols IBKR can’t resolve). Buy = Stage&nbsp;1→2, Sell = Stage&nbsp;3→4.</p>
    <p class="foot__gen">Generated by jtrade · account guarded by DRY_RUN · scan {{DATE}} {{TIME}}</p>
  </footer>
</main>

<script>
{{JS}}
</script>
"""

_CSS = r"""
*{box-sizing:border-box}
:root{
  --font-ui: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace;

  --bg:#e9edec; --panel:#ffffff; --panel-2:#f2f6f5; --raise:#ffffff;
  --text:#132321; --muted:#5f6f6d; --faint:#8a9997;
  --border:#dae1df; --border-2:#c8d2cf;
  --accent:#0b8f86; --accent-ink:#0a6f68; --accent-bg:#dbefed;
  --s1:#3f6fd0; --s1-bg:#e5ecfb;
  --s2:#178a4c; --s2-bg:#dff1e7;
  --s3:#b0771a; --s3-bg:#f6ecd4;
  --s4:#c33e3b; --s4-bg:#f9e1e0;
  --up:#178a4c; --down:#c33e3b;
  --shadow:0 1px 2px rgba(15,40,38,.05), 0 14px 34px -20px rgba(15,40,38,.28);
  --radius:14px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0a1216; --panel:#101b20; --panel-2:#0d171b; --raise:#14232a;
    --text:#e7efee; --muted:#96a8a6; --faint:#6b7f7d;
    --border:#213036; --border-2:#2c3f47;
    --accent:#2ed3c6; --accent-ink:#7fe9e0; --accent-bg:#0e2a29;
    --s1:#6f9bf0; --s1-bg:#152238; --s2:#3fc584; --s2-bg:#112a1e;
    --s3:#e2ac48; --s3-bg:#2a2210; --s4:#ec726c; --s4-bg:#2c1615;
    --up:#3fc584; --down:#ec726c;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 18px 40px -22px rgba(0,0,0,.7);
  }
}
:root[data-theme="light"]{
  --bg:#e9edec; --panel:#ffffff; --panel-2:#f2f6f5; --raise:#ffffff;
  --text:#132321; --muted:#5f6f6d; --faint:#8a9997;
  --border:#dae1df; --border-2:#c8d2cf;
  --accent:#0b8f86; --accent-ink:#0a6f68; --accent-bg:#dbefed;
  --s1:#3f6fd0; --s1-bg:#e5ecfb; --s2:#178a4c; --s2-bg:#dff1e7;
  --s3:#b0771a; --s3-bg:#f6ecd4; --s4:#c33e3b; --s4-bg:#f9e1e0;
  --up:#178a4c; --down:#c33e3b;
  --shadow:0 1px 2px rgba(15,40,38,.05), 0 14px 34px -20px rgba(15,40,38,.28);
}
:root[data-theme="dark"]{
  --bg:#0a1216; --panel:#101b20; --panel-2:#0d171b; --raise:#14232a;
  --text:#e7efee; --muted:#96a8a6; --faint:#6b7f7d;
  --border:#213036; --border-2:#2c3f47;
  --accent:#2ed3c6; --accent-ink:#7fe9e0; --accent-bg:#0e2a29;
  --s1:#6f9bf0; --s1-bg:#152238; --s2:#3fc584; --s2-bg:#112a1e;
  --s3:#e2ac48; --s3-bg:#2a2210; --s4:#ec726c; --s4-bg:#2c1615;
  --up:#3fc584; --down:#ec726c;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 18px 40px -22px rgba(0,0,0,.7);
}

html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:var(--font-ui); line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.mono{font-family:var(--font-mono); font-variant-numeric:tabular-nums; letter-spacing:-.01em}
h1,h2,h3{margin:0; text-wrap:balance; letter-spacing:-.02em}

.topbar{
  position:sticky; top:0; z-index:20;
  display:flex; align-items:center; justify-content:space-between; gap:1rem;
  padding:.72rem clamp(1rem,4vw,2.4rem);
  background:color-mix(in srgb, var(--panel) 82%, transparent);
  backdrop-filter:saturate(1.4) blur(10px);
  border-bottom:1px solid var(--border);
}
.brand{display:flex; align-items:baseline; gap:.6rem}
.brand__mark{font-weight:700; font-size:1.06rem; color:var(--text)}
.brand__mark::before{content:"›"; color:var(--accent); margin-right:.15rem; font-weight:700}
.brand__eyebrow{font-size:.66rem; text-transform:uppercase; letter-spacing:.22em; color:var(--muted)}
.topbar__right{display:flex; align-items:center; gap:.8rem}
.acct{display:flex; gap:.4rem}
.acct__chip{display:flex; flex-direction:column; padding:.2rem .6rem; border:1px solid var(--border); border-radius:9px; background:var(--panel-2)}
.acct__k{font-size:.6rem; text-transform:uppercase; letter-spacing:.14em; color:var(--faint)}
.acct__v{font-size:.82rem; font-weight:600}
.theme{
  display:inline-flex; align-items:center; gap:.4rem; cursor:pointer;
  border:1px solid var(--border); background:var(--panel-2); color:var(--muted);
  border-radius:9px; padding:.35rem .6rem; font:inherit; font-size:.72rem;
}
.theme__dot{width:.7rem; height:.7rem; border-radius:50%; background:var(--accent); box-shadow:0 0 0 3px var(--accent-bg)}
.theme:hover{border-color:var(--border-2); color:var(--text)}
.theme:focus-visible, .seg-btn:focus-visible, .stage-chip:focus-visible, input:focus-visible{outline:2px solid var(--accent); outline-offset:2px}

main{max-width:1160px; margin:0 auto; padding:clamp(1.2rem,3vw,2.6rem) clamp(1rem,4vw,2.4rem) 4rem}

.eyebrow{margin:0 0 .5rem; font-size:.7rem; text-transform:uppercase; letter-spacing:.2em; color:var(--accent-ink); font-weight:600}
.hero{display:grid; grid-template-columns:1.3fr 1fr; gap:2rem clamp(1.5rem,4vw,3rem); align-items:end; padding-bottom:1.6rem}
.hero h1{font-size:clamp(2.1rem,5vw,3.1rem); font-weight:680; line-height:1.02}
.hero__sub{margin:.7rem 0 0; color:var(--muted); max-width:46ch; font-size:.96rem}
.kpis{display:grid; grid-template-columns:repeat(4,1fr); gap:.6rem}
.kpi{display:flex; flex-direction:column; gap:.15rem; padding:.75rem .8rem; background:var(--panel); border:1px solid var(--border); border-radius:11px}
.kpi__n{font-size:1.7rem; font-weight:650; line-height:1}
.kpi__l{font-size:.66rem; text-transform:uppercase; letter-spacing:.12em; color:var(--muted)}
.kpi--buy{border-color:color-mix(in srgb,var(--s2) 45%,var(--border)); background:var(--s2-bg)}
.kpi--buy .kpi__n{color:var(--s2)}
.kpi--sell .kpi__n{color:var(--faint)}

.mix{margin:1.4rem 0 2.4rem; padding:1.1rem 1.2rem; background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow)}
.mix__head{display:flex; justify-content:space-between; align-items:baseline; margin-bottom:.7rem}
.mix__title{font-size:.72rem; text-transform:uppercase; letter-spacing:.16em; color:var(--muted)}
.mix__total{font-size:.78rem; color:var(--faint)}
.meter{display:flex; height:11px; border-radius:6px; overflow:hidden; background:var(--panel-2); gap:2px}
.meter--lg{height:16px}
.seg{display:block; height:100%}
.seg.s1{background:var(--s1)} .seg.s2{background:var(--s2)} .seg.s3{background:var(--s3)} .seg.s4{background:var(--s4)}
.legend{display:flex; flex-wrap:wrap; gap:.4rem 1.4rem; margin:.8rem 0 0; padding:0; list-style:none; font-size:.82rem; color:var(--muted)}
.legend li{display:flex; align-items:center; gap:.45rem}
.legend b{color:var(--text); font-weight:600}
.dot{width:.6rem; height:.6rem; border-radius:50%; flex:none}
.dot.s1{background:var(--s1)} .dot.s2{background:var(--s2)} .dot.s3{background:var(--s3)} .dot.s4{background:var(--s4)}

.rm{display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:.5rem .8rem}
.rm__item{display:flex; align-items:baseline; justify-content:space-between; gap:.6rem; padding:.45rem .7rem; background:var(--panel-2); border:1px solid var(--border); border-radius:8px; font-size:.82rem}
.rm__metric{color:var(--text); font-weight:600; white-space:nowrap}
.rm__desc{color:var(--muted); text-align:right}

.section-head{display:flex; flex-direction:column; gap:.2rem; margin-bottom:1.1rem}
.section-head h2{font-size:1.35rem; font-weight:640}
.section-head p{margin:0; color:var(--muted); font-size:.92rem}

.signals{margin-bottom:2.8rem}
.signals__grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:1rem}
.signal{position:relative; padding:1.15rem 1.2rem 1.2rem; background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden}
.signal::before{content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--s2)}
.signal--empty{display:flex; align-items:center; color:var(--muted); box-shadow:none}
.signal--empty::before{background:var(--border-2)}
.signal--empty p{margin:0; font-size:.92rem}
.signal__head{display:flex; align-items:center; justify-content:space-between; margin-bottom:.5rem}
.signal__id{display:flex; align-items:baseline; gap:.6rem}
.tkr{font-family:var(--font-mono); font-size:1.55rem; font-weight:700; letter-spacing:-.02em}
.signal__sheet{font-size:.68rem; text-transform:uppercase; letter-spacing:.1em; color:var(--faint)}
.signal__stage{display:flex; align-items:center; gap:.45rem; font-size:.82rem; color:var(--muted); margin-bottom:.9rem}
.spec{display:grid; grid-template-columns:repeat(3,1fr); gap:.7rem .4rem; margin:0 0 1rem}
.spec div{display:flex; flex-direction:column; gap:.12rem}
.spec dt{font-size:.63rem; text-transform:uppercase; letter-spacing:.1em; color:var(--faint)}
.spec dd{margin:0; font-size:1rem; font-weight:600; display:flex; align-items:baseline; gap:.35rem}
.spec .sub{font-size:.66rem; font-weight:500; color:var(--muted); letter-spacing:0}
.spec .sub.down{color:var(--down)}
.strength{display:flex; align-items:center; gap:.6rem; margin-bottom:.85rem}
.strength__label{font-size:.66rem; text-transform:uppercase; letter-spacing:.1em; color:var(--faint); flex:none}
.strength__track{flex:1; height:6px; border-radius:4px; background:var(--panel-2); overflow:hidden}
.strength__fill{display:block; height:100%; background:linear-gradient(90deg,var(--accent),var(--s2))}
.strength__val{font-size:.82rem; font-weight:600}
.signal__note{margin:0; font-size:.78rem; color:var(--muted); line-height:1.45; border-top:1px solid var(--border); padding-top:.7rem}

.pill{display:inline-block; padding:.12rem .5rem; border-radius:6px; font-size:.68rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase}
.pill--buy{background:var(--s2); color:#fff}
.pill--sell{background:var(--s4); color:#fff}
.pill--watch{background:var(--panel-2); color:var(--muted); border:1px solid var(--border)}
.hold{font-size:.78rem; color:var(--faint)}

.compass{margin-bottom:2rem; padding:1.2rem 1.3rem 1.4rem; background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow)}
.compass__map{margin:0 0 1rem; color:var(--muted); font-size:.82rem}
.compass__scenarios{display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:.8rem; margin-bottom:1rem}
.compass__scenario{padding:.8rem .9rem; background:var(--panel-2); border:1px solid var(--border); border-radius:10px; opacity:.72}
.compass__scenario.is-lead{opacity:1; border-color:color-mix(in srgb, var(--accent) 45%, var(--border))}
.compass__sc-head{display:flex; justify-content:space-between; align-items:baseline; margin-bottom:.4rem; font-weight:600}
.compass__sc-bar{height:6px; border-radius:4px; background:var(--bg); overflow:hidden; margin-bottom:.5rem}
.compass__sc-bar span{display:block; height:100%}
.compass__sc-summary{margin:0; font-size:.78rem; color:var(--muted); line-height:1.4}
.compass__conclusion{margin:0 0 1.1rem; font-size:.88rem; line-height:1.5}
.compass__grid{display:grid; grid-template-columns:1.4fr 1fr; gap:1.4rem}
.compass__premises h3, .compass__side h3{font-size:.72rem; text-transform:uppercase; letter-spacing:.14em; color:var(--muted); margin:0 0 .6rem}
.compass__premises ul{list-style:none; margin:0 0 0; padding:0; display:flex; flex-direction:column; gap:.6rem}
.compass__premises li{display:flex; gap:.55rem; align-items:flex-start; font-size:.84rem}
.compass__premises li p{margin:.1rem 0 0; color:var(--muted); font-size:.8rem; line-height:1.4}
.compass__lean-icon{flex:none; font-size:.7rem; line-height:1.6}
.lean-bullish .compass__lean-icon{color:var(--up)}
.lean-bearish .compass__lean-icon{color:var(--down)}
.lean-neutral .compass__lean-icon{color:var(--faint)}
.compass__side table{margin-bottom:1.1rem}
.compass__side td{padding:.35rem .5rem; border-bottom:1px solid var(--border); font-size:.8rem; vertical-align:top}
.compass__side td:nth-child(2){color:var(--muted)}
.compass__catalysts{margin:0; padding-left:1.1rem; font-size:.8rem; color:var(--muted); display:flex; flex-direction:column; gap:.3rem}
@media (max-width:820px){.compass__grid{grid-template-columns:1fr}}
.montag{display:inline-grid; place-items:center; width:1.05rem; height:1.05rem; margin-left:.4rem; border-radius:4px; background:var(--panel-2); border:1px solid var(--border); color:var(--faint); font-size:.6rem; font-weight:700; vertical-align:middle}

.controls{position:sticky; top:56px; z-index:10; display:flex; flex-wrap:wrap; gap:.7rem; align-items:center; margin-bottom:1rem; padding:.6rem; background:color-mix(in srgb,var(--bg) 88%,transparent); backdrop-filter:blur(6px); border-radius:11px}
.seg-toggle{display:inline-flex; background:var(--panel-2); border:1px solid var(--border); border-radius:9px; padding:3px}
.seg-btn{border:0; background:transparent; color:var(--muted); font:inherit; font-size:.8rem; font-weight:600; padding:.35rem .8rem; border-radius:7px; cursor:pointer}
.seg-btn.is-active{background:var(--raise); color:var(--text); box-shadow:0 1px 2px rgba(0,0,0,.12)}
.stage-filter{display:inline-flex; align-items:center; gap:.3rem; padding:3px 3px 3px .7rem; background:var(--panel-2); border:1px solid var(--border); border-radius:9px}
.stage-filter__lbl{font-size:.62rem; text-transform:uppercase; letter-spacing:.12em; color:var(--faint); margin-right:.15rem}
.stage-chip{display:inline-flex; align-items:center; gap:.32rem; border:1px solid transparent; background:transparent; color:var(--muted); font:inherit; font-size:.8rem; font-weight:600; padding:.28rem .55rem; border-radius:7px; cursor:pointer; opacity:.5; transition:opacity .12s, background .12s}
.stage-chip .dot{transition:transform .12s}
.stage-chip.is-on{opacity:1; background:var(--raise); box-shadow:0 1px 2px rgba(0,0,0,.1)}
.stage-chip.is-on.s1{color:var(--s1)} .stage-chip.is-on.s2{color:var(--s2)}
.stage-chip.is-on.s3{color:var(--s3)} .stage-chip.is-on.s4{color:var(--s4)}
.stage-chip:not(.is-on) .dot{transform:scale(.7)}
.stage-chip:hover{opacity:.85}
.search{margin-left:auto}
.search input{width:190px; max-width:44vw; padding:.42rem .7rem; font:inherit; font-size:.82rem; color:var(--text); background:var(--panel); border:1px solid var(--border); border-radius:9px}
.search input::placeholder{color:var(--faint)}

.panels{display:flex; flex-direction:column; gap:1rem}
.panel{background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden}
.panel__head{display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:.7rem 1rem; border-bottom:1px solid var(--border); background:var(--panel-2)}
.panel__head h3{font-size:.92rem; font-weight:640}
.panel__meta{display:flex; align-items:center; gap:.7rem}
.panel__sig{font-size:.64rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--s2); background:var(--s2-bg); padding:.12rem .45rem; border-radius:5px}
.panel__count{font-size:.76rem; color:var(--faint)}
.panel__meter{display:flex; width:96px; height:7px; border-radius:4px; overflow:hidden; background:var(--bg); gap:1.5px}
.tablewrap{overflow-x:auto}
table{width:100%; border-collapse:collapse; font-size:.85rem}
thead th{position:sticky; top:0; text-align:left; font-size:.62rem; text-transform:uppercase; letter-spacing:.1em; color:var(--faint); font-weight:600; padding:.5rem 1rem; background:var(--panel); border-bottom:1px solid var(--border)}
th.num, td.num{text-align:right}
tbody td{padding:.44rem 1rem; border-bottom:1px solid var(--border)}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--panel-2)}
.c-tkr .tkrname{font-family:var(--font-mono); font-weight:600}
td.num{font-size:.82rem}
td.up{color:var(--up)} td.down{color:var(--down)}
.stage{display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem; color:var(--text)}
.stage--na{color:var(--faint)}
.stage.s1{color:var(--s1)} .stage.s2{color:var(--s2)} .stage.s3{color:var(--s3)} .stage.s4{color:var(--s4)}
.stage__name{color:var(--muted); font-weight:400}
tr.is-buy{background:var(--s2-bg)}
tr.is-buy td:first-child{box-shadow:inset 3px 0 0 var(--s2)}
tr.is-sell{background:var(--s4-bg)}
tr.is-sell td:first-child{box-shadow:inset 3px 0 0 var(--s4)}
tr.is-mon td{color:var(--faint)}
tr.is-mon .tkrname{color:var(--muted)}
tr[hidden]{display:none}
.panel[hidden]{display:none}
.empty{text-align:center; color:var(--muted); padding:2rem}

.foot{margin-top:2.4rem; padding-top:1.2rem; border-top:1px solid var(--border); color:var(--muted); font-size:.78rem; display:flex; flex-direction:column; gap:.3rem}
.foot .montag{width:1rem; height:1rem}
.foot__gen{color:var(--faint); font-size:.72rem}

@media (max-width:820px){
  .hero{grid-template-columns:1fr; align-items:start}
  .acct{display:none}
  .stage__name{display:none}
  .controls{top:52px}
  .search{margin-left:0}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

_JS = r"""
(function(){
  var root=document.documentElement;
  var btn=document.getElementById('theme');
  function cur(){return root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
  btn.addEventListener('click',function(){root.setAttribute('data-theme', cur()==='dark'?'light':'dark');});

  var mode='all', q='';
  var stages={1:true,2:true,3:true,4:true};
  var panels=[].slice.call(document.querySelectorAll('.panel'));
  var empty=document.getElementById('empty');

  function apply(){
    var anyVisible=false;
    panels.forEach(function(p){
      var shown=0;
      [].slice.call(p.querySelectorAll('tbody tr')).forEach(function(tr){
        var sig=tr.getAttribute('data-signal');
        var st=parseInt(tr.getAttribute('data-stage'),10)||0;
        var tkr=tr.getAttribute('data-tkr').toLowerCase();
        var ok=true;
        if(mode==='signals' && sig==='Hold') ok=false;
        if(st!==0 && !stages[st]) ok=false;      // stage filter (N/A rows always pass)
        if(q && tkr.indexOf(q)===-1) ok=false;
        tr.hidden=!ok;
        if(ok) shown++;
      });
      p.hidden = shown===0;
      if(shown>0) anyVisible=true;
    });
    empty.hidden=anyVisible;
  }

  document.querySelectorAll('.seg-btn').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.seg-btn').forEach(function(x){x.classList.remove('is-active');});
      b.classList.add('is-active'); mode=b.getAttribute('data-mode'); apply();
    });
  });
  document.querySelectorAll('.stage-chip').forEach(function(c){
    c.addEventListener('click',function(){
      var s=parseInt(c.getAttribute('data-stage'),10);
      stages[s]=!stages[s];
      c.classList.toggle('is-on', stages[s]);
      c.setAttribute('aria-pressed', stages[s]?'true':'false');
      apply();
    });
  });
  document.getElementById('q').addEventListener('input',function(e){q=e.target.value.trim().toLowerCase(); apply();});
})();
"""
