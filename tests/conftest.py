from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def unity_local_settings(monkeypatch):
    """Use the committed template, not this machine's local.yaml, in every test."""
    template = Path(__file__).resolve().parents[1] / "src/runners/unityhpc/connections"
    monkeypatch.setenv("UNITYHPC_LOCAL_CONFIG", str(template / "local.example.yaml"))
    monkeypatch.delenv("UNITYHPC_CONTROLLER_URL", raising=False)
    monkeypatch.delenv("UNITYHPC_RUNTIME_ROOT", raising=False)
