"""Render the screen result as Markdown + HTML, organized by sheet/angle.

Columns per row: TKT | Price | Change | Stage | Signal | Moving Average Road Map
(% distance to 5/10/20-day EMA and 50/100/200/250-day SMA). Buy/Sell rows are
visually flagged. Files are written to ``report.out_dir`` timestamped, plus stable
``latest.md`` / ``latest.html`` copies. The workbook is never modified.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

from .config import Settings
from .criteria.stage_analysis import STAGE_DESCRIPTIONS
from .indicators import MA_ROADMAP
from .screen import ScreenResult, ScreenRow


def _fmt_price(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _fmt_change(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _ma_class(v) -> str:
    if v is None:
        return ""
    return " up" if v >= 0 else " down"


def _stage_label(stage) -> str:
    if stage is None:
        return "—"
    return f"{stage} · {STAGE_DESCRIPTIONS.get(stage, '?').split(' (')[0]}"


_MD_TEMPLATE = """# jtrade scan — {{ generated_at }}

**Universe:** {{ n_symbols }} symbols across {{ n_sheets }} sheets ·
**Buy:** {{ n_buy }} · **Sell:** {{ n_sell }}

## Moving Average Road Map

| Metric | Label |
|-----|-----|
{% for key, label, kind, period, desc in ma_roadmap -%}
| {{ period }}-day {{ 'EMA' if kind == 'ema' else 'SMA' }} | {{ desc }} |
{% endfor %}
{% for sheet, rows in by_sheet.items() %}
## {{ sheet }}

| TKT | Price | Change | Stage | Signal |{% for key, label, kind, period, desc in ma_roadmap %} {{ label }} |{% endfor %}{{ "" }}
|-----|------:|-------:|-------|--------|{% for _ in ma_roadmap %}------:|{% endfor %}{{ "" }}
{% for r in rows -%}
| {{ r.ticker }}{% if not r.tradable %} ᵐ{% endif %} | {{ price(r.price) }} | {{ change(r.change_pct) }} | {{ stage(r.stage) }} | {{ signal_md(r) }} |{% for key, label, kind, period, desc in ma_roadmap %} {{ ma(r.ma_diffs.get(key)) }} |{% endfor %}{{ "" }}
{% endfor %}
{% endfor %}
> ᵐ = monitor-only (no order proposals). Signals: **Buy** = Stage 1→2, **Sell** = Stage 3→4.
"""

_HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>jtrade scan — {{ generated_at }}</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;max-width:1100px}
  h1{margin-bottom:.2rem}.meta{color:#666;margin-bottom:1.5rem}
  h2{margin-top:2rem;border-bottom:2px solid #eee;padding-bottom:.3rem}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{padding:.4rem .6rem;border-bottom:1px solid #eee;text-align:left}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .buy{background:#e6f7ed;font-weight:600}.sell{background:#fdeaea;font-weight:600}
  .up{color:#0a7d33}.down{color:#c0392b}.mon{color:#999}
  .badge{display:inline-block;padding:.05rem .4rem;border-radius:.4rem;font-size:12px}
  .badge.buy{background:#0a7d33;color:#fff}.badge.sell{background:#c0392b;color:#fff}
  .roadmap{width:auto;margin-bottom:1.5rem}
</style>
<h1>jtrade scan</h1>
<div class="meta">{{ generated_at }} · {{ n_symbols }} symbols / {{ n_sheets }} sheets ·
  Buy {{ n_buy }} · Sell {{ n_sell }}</div>

<h2>Moving Average Road Map</h2>
<table class="roadmap"><thead><tr><th>Metric</th><th>Label</th></tr></thead>
<tbody>
{% for key, label, kind, period, desc in ma_roadmap %}
<tr><td>{{ period }}-day {{ 'EMA' if kind == 'ema' else 'SMA' }}</td><td>{{ desc }}</td></tr>
{% endfor %}
</tbody></table>
{% for sheet, rows in by_sheet.items() %}
<h2>{{ sheet }}</h2>
<table><thead><tr><th>TKT</th><th>Price</th><th>Change</th><th>Stage</th><th>Signal</th>{% for key, label, kind, period, desc in ma_roadmap %}<th title="{{ desc }}">{{ label }}</th>{% endfor %}</tr></thead>
<tbody>
{% for r in rows %}
<tr class="{{ 'buy' if r.signal=='Buy' else 'sell' if r.signal=='Sell' else '' }}">
  <td>{{ r.ticker }}{% if not r.tradable %} <span class="mon">ᵐ</span>{% endif %}</td>
  <td class="num">{{ price(r.price) }}</td>
  <td class="num {{ 'up' if (r.change_pct or 0)>=0 else 'down' }}">{{ change(r.change_pct) }}</td>
  <td>{{ stage(r.stage) }}</td>
  <td>{% if r.signal=='Buy' %}<span class="badge buy">Buy</span>{% elif r.signal=='Sell' %}<span class="badge sell">Sell</span>{% else %}Hold{% endif %}</td>
  {% for key, label, kind, period, desc in ma_roadmap %}<td class="num{{ ma_cls(r.ma_diffs.get(key)) }}">{{ ma(r.ma_diffs.get(key)) }}</td>{% endfor %}
</tr>
{% endfor %}
</tbody></table>
{% endfor %}
<p class="mon">ᵐ = monitor-only (no order proposals). Buy = Stage 1→2, Sell = Stage 3→4.</p>
"""


def _signal_md(r: ScreenRow) -> str:
    if r.signal == "Buy":
        return "🟢 **Buy**"
    if r.signal == "Sell":
        return "🔴 **Sell**"
    return "Hold"


def render(result: ScreenResult, settings: Settings) -> dict[str, Path]:
    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    ctx = {
        "generated_at": result.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "by_sheet": result.by_sheet,
        "n_sheets": len(result.by_sheet),
        "n_symbols": len({r.ticker for r in result.rows}),
        "n_buy": len(result.signals("Buy")),
        "n_sell": len(result.signals("Sell")),
        "price": _fmt_price,
        "change": _fmt_change,
        "ma": _fmt_change,
        "ma_cls": _ma_class,
        "ma_roadmap": MA_ROADMAP,
        "stage": _stage_label,
        "signal_md": _signal_md,
    }
    md = env.from_string(_MD_TEMPLATE).render(**ctx)
    html = env.from_string(_HTML_TEMPLATE).render(**ctx)

    out_dir: Path = settings.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.generated_at.strftime("%Y%m%d_%H%M%S")
    paths = {
        "markdown": out_dir / f"scan_{stamp}.md",
        "html": out_dir / f"scan_{stamp}.html",
        "latest_md": out_dir / "latest.md",
        "latest_html": out_dir / "latest.html",
    }
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["html"].write_text(html, encoding="utf-8")
    paths["latest_md"].write_text(md, encoding="utf-8")
    paths["latest_html"].write_text(html, encoding="utf-8")
    return paths
