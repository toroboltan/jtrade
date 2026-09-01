"""Load and validate settings.yaml, resolving relative paths against the project root.

The project root is the directory that contains ``config/settings.yaml`` (i.e. the
``jtrade/`` project directory), located by walking up from this file. All path-like
settings (workbook, bars DB, report dir, state files) are resolved to absolute
paths so the CLI and MCP server behave identically regardless of cwd.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# jtrade/jtrade/config.py -> project root is two parents up (jtrade/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _resolve(path_like: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path_like)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


@dataclass
class Settings:
    raw: dict[str, Any]
    path: Path
    project_root: Path = field(default=PROJECT_ROOT)

    # ---- convenience accessors (validated) ----
    @property
    def ibkr(self) -> dict[str, Any]:
        return self.raw["ibkr"]

    @property
    def data(self) -> dict[str, Any]:
        return self.raw["data"]

    @property
    def universe(self) -> dict[str, Any]:
        return self.raw["universe"]

    @property
    def criteria(self) -> dict[str, Any]:
        return self.raw["criteria"]

    @property
    def risk(self) -> dict[str, Any]:
        return self.raw["risk"]

    @property
    def execute(self) -> dict[str, Any]:
        return self.raw["execute"]

    @property
    def report(self) -> dict[str, Any]:
        return self.raw["report"]

    # ---- resolved paths ----
    @property
    def workbook_path(self) -> Path:
        return _resolve(self.universe["workbook"])

    @property
    def db_path(self) -> Path:
        return _resolve(self.data["db_path"])

    @property
    def report_dir(self) -> Path:
        return _resolve(self.report["out_dir"])

    @property
    def market_compass_file(self) -> Path:
        """Curated weekly newsletter-scenario data for the dashboard's Market Compass
        section. Overridable via report.market_compass_file; missing file is fine —
        the section just doesn't render."""
        override = self.report.get("market_compass_file")
        return _resolve(override) if override else self.project_root / "market-compass" / "current.yaml"

    @property
    def pending_orders_path(self) -> Path:
        return _resolve(self.execute["pending_orders"])

    @property
    def audit_log_path(self) -> Path:
        return _resolve(self.execute["audit_log"])

    @property
    def dry_run(self) -> bool:
        """DRY_RUN env var overrides the settings default when set."""
        env = os.environ.get("DRY_RUN")
        if env is not None:
            return env.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(self.execute.get("dry_run", True))


_REQUIRED_TOP_KEYS = ["ibkr", "data", "universe", "criteria", "risk", "execute", "report"]


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Read + validate settings.yaml. Raises ValueError on missing required keys."""
    settings_path = Path(path) if path else DEFAULT_SETTINGS_PATH
    if not settings_path.exists():
        raise FileNotFoundError(f"settings file not found: {settings_path}")

    with settings_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    missing = [k for k in _REQUIRED_TOP_KEYS if k not in raw]
    if missing:
        raise ValueError(f"settings.yaml missing required sections: {missing}")

    # spot-check a few critical numeric params so failures are early and clear
    risk = raw["risk"]
    for key in ("equity_risk_per_trade", "max_aggregate_risk", "max_new_proposals", "min_cash_reserve"):
        if key not in risk:
            raise ValueError(f"settings.yaml risk.{key} is required")
    if not (0 < risk["equity_risk_per_trade"] < 1):
        raise ValueError("risk.equity_risk_per_trade must be a fraction in (0, 1)")

    port = raw["ibkr"].get("port")
    if port not in (4001, 4002) and not isinstance(port, int):
        raise ValueError("ibkr.port must be an integer (4001 live / 4002 paper typical)")

    return Settings(raw=raw, path=settings_path)
