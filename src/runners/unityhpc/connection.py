"""Merge a shared Unity hardware profile with this machine's untracked settings."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONNECTIONS = Path(__file__).resolve().parent / "connections"
# Settings that differ per user or per device. They live in connections/local.yaml,
# never in the tracked hardware profiles.
LOCAL_KEYS = frozenset(
    {"controller_url", "runtime_root", "server_runtime_root", "model_cache", "workspace_lease"}
)
ENVIRONMENT = {
    "controller_url": "UNITYHPC_CONTROLLER_URL",
    "runtime_root": "UNITYHPC_RUNTIME_ROOT",
}


def local_settings_path() -> Path:
    return Path(os.environ.get("UNITYHPC_LOCAL_CONFIG") or CONNECTIONS / "local.yaml")


def load_connection(profile: Path) -> dict:
    """Return the profile, then local.yaml, then environment overrides, merged in that order."""
    config = yaml.safe_load(Path(profile).read_text())
    if type(config) is not dict:
        raise ValueError(f"{profile} must be a mapping")
    local_path = local_settings_path()
    if local_path.is_file():
        local = yaml.safe_load(local_path.read_text()) or {}
        if type(local) is not dict:
            raise ValueError(f"{local_path} must be a mapping")
        if unknown := sorted(set(local) - LOCAL_KEYS):
            raise ValueError(f"{local_path} has keys that belong in a profile: {unknown}")
        config.update(local)
    for key, variable in ENVIRONMENT.items():
        if value := os.environ.get(variable):
            config[key] = value
    if missing := [key for key in ENVIRONMENT if not config.get(key)]:
        raise ValueError(
            f"Unity connection is missing {missing}. Copy {CONNECTIONS / 'local.example.yaml'} "
            f"to {CONNECTIONS / 'local.yaml'} and set your values, or set "
            + " and ".join(ENVIRONMENT[key] for key in missing)
        )
    return config
