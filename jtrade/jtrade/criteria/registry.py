"""Discover and instantiate the criteria enabled in settings.yaml.

Discovery walks every module in this package, finds ``Criterion`` subclasses, and keys
them by their module-level ``NAME`` (falling back to the class ``name``). To add a
criterion: drop a new file here subclassing ``Criterion`` with a ``NAME`` constant, then
add that name to ``criteria.enabled`` in settings.yaml. No engine changes required.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Type

from ..config import Settings
from .base import Criterion


def _discover() -> dict[str, Type[Criterion]]:
    found: dict[str, Type[Criterion]] = {}
    package = importlib.import_module(__package__)
    for mod_info in pkgutil.iter_modules(package.__path__):
        if mod_info.name in ("base", "registry", "__init__"):
            continue
        module = importlib.import_module(f"{__package__}.{mod_info.name}")
        name = getattr(module, "NAME", None)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Criterion) and obj is not Criterion:
                key = name or getattr(obj, "name", obj.__name__)
                found[key] = obj
    return found


def load_enabled(settings: Settings) -> list[Criterion]:
    """Instantiate each enabled criterion with its per-criterion config block."""
    registry = _discover()
    cconf = settings.criteria
    enabled = cconf.get("enabled", [])
    instances: list[Criterion] = []
    for name in enabled:
        cls = registry.get(name)
        if cls is None:
            raise ValueError(
                f"criteria.enabled references unknown criterion '{name}'. "
                f"Available: {sorted(registry)}"
            )
        params = cconf.get(name, {}) or {}
        instances.append(cls(**params))
    return instances


def available(settings: Settings | None = None) -> list[str]:
    return sorted(_discover().keys())
