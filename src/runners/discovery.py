"""Load an execution adapter from the repository's portable registry."""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def load_runner(root: Path, name: str, config: Path):
    registry = json.loads((root / "src/runners/registry.json").read_text())
    if name not in registry:
        raise ValueError(f"unknown runner: {name}")
    module, symbol = registry[name].split(":", 1)
    return getattr(importlib.import_module(module), symbol)(config)
