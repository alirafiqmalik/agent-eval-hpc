import copy
import hashlib
import json
from pathlib import Path

import pytest

from runners.budget import Budget
from runners.interface import ExecutionRequest
from runners.unityhpc.adapter import UnityRunner
from runners.unityhpc.client import Client, ControllerError

CPU_HARDWARE = {
    "id": "unity-cpu-v1",
    "gpu_count": 0,
    "allocated_cpus": 4,
    "allocated_memory_mb": 8192,
}
UNLIMITED = {"gpu_hours": None, "cpu_hours": None}


@pytest.mark.parametrize(
    "changed", [None, "identity", "argv", "environment", "outputs", "hardware", "source", "hash"]
)
def test_unity_recovery_attests_exact_request_and_source(tmp_path, monkeypatch, changed):
    root = Path(__file__).resolve().parents[1]
    runner = UnityRunner(
        root / "src/runners/unityhpc/connections/config.yaml",
        Budget(tmp_path / "budget.json", **UNLIMITED),
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    request = ExecutionRequest(
        "synthetic-recovery",
        bundle,
        ("python", "task.py"),
        ("results.jsonl",),
        {"id": "synthetic-cpu", "gpu_count": 0, "allocated_cpus": 8, "allocated_memory_mb": 32768},
        600,
        environment={"EVAL_RUN_ID": "synthetic-run"},
    )
    expected = runner.declaration(request)
    expected["tasks"][0]["resources"]["constraint"] = ""
    source = "a" * 64
    monkeypatch.setattr(runner, "plan", lambda request: {"bundle": {"sha256": source}})
    observation = {
        "id": request.id,
        "request_hash": hashlib.sha256(
            json.dumps(
                {"spec": expected, "source_sha256": source}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "spec": {
            **copy.deepcopy(expected),
            "backend": {"synthetic": True},
            "bundle_manifest": {"sha256": source},
        },
    }
    task = observation["spec"]["tasks"][0]
    if changed == "identity":
        observation["id"] = "another-execution"
    elif changed == "argv":
        task["argv"] = ["python", "another-task.py"]
    elif changed == "environment":
        task["env"]["EVAL_RUN_ID"] = "another-run"
    elif changed == "outputs":
        task["outputs"] = ["different.jsonl"]
    elif changed == "hardware":
        task["resources"]["memory_mb"] = 65536
    elif changed == "source":
        observation["spec"]["bundle_manifest"]["sha256"] = "b" * 64
    elif changed == "hash":
        observation["request_hash"] = "c" * 64
    assert runner.attest(request, observation)["matched"] is (changed is None)


def test_unity_connection_accepts_per_user_controller_and_runtime(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("UNITYHPC_CONTROLLER_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("UNITYHPC_RUNTIME_ROOT", "/work/another-user/eval-runtime")
    runner = UnityRunner(
        root / "src/runners/unityhpc/connections/short.yaml",
        Budget(tmp_path / "budget.json", **UNLIMITED),
    )
    request = ExecutionRequest(
        "other-user", tmp_path, ("true",), ("result.json",), CPU_HARDWARE, 60
    )
    assert runner.client.url == "http://127.0.0.1:9999"
    assert runner.declaration(request)["tasks"][0]["env"]["EVAL_RUNTIME_ROOT"] == (
        "/work/another-user/eval-runtime"
    )


def test_external_connection_keeps_budget_in_project(tmp_path):
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "src/runners/unityhpc/connections/short.yaml").read_text())
    external = tmp_path / "personal-connection.yaml"
    external.write_text(yaml.safe_dump(config))
    runner = UnityRunner(external)
    assert runner.budget.path == root / ".cache/budget.json"


def test_retries_reserve_the_full_budget_and_duplicates_do_not_double_charge(tmp_path):
    budget = Budget(tmp_path / "budget.json", gpu_hours=1, cpu_hours=None)
    budget.reserve("same", 1, 1800, 2)
    budget.reserve("same", 1, 1800, 2)
    with pytest.raises(ValueError, match="exhausted"):
        budget.reserve("another", 1, 1)
    assert json.loads((tmp_path / "budget.json").read_text())["same"]["gpu_seconds"] == 3600


def test_settled_usage_preserves_hold_and_never_double_refunds(tmp_path):
    budget = Budget(tmp_path / "budget.json", gpu_hours=1 / 6, cpu_hours=None)
    budget.reserve("finished", 1, 600)
    proof = {"job_id": "1", "state": "FAILED"}
    budget.settle("finished", 1, 61, proof)
    budget.settle("finished", 1, 61, proof)
    budget.reserve("finished", 1, 600)
    budget.reserve("next", 1, 480)
    ledger = json.loads(budget.path.read_text())
    assert ledger["finished"]["gpu_seconds"] == 120
    assert ledger["finished"]["reservation"]["gpu_seconds"] == 600
    assert sum(row["gpu_seconds"] for row in ledger.values()) == 600
    with pytest.raises(ValueError, match="exhausted"):
        budget.reserve("overspend", 1, 1)
    with pytest.raises(ValueError, match="evidence changed"):
        budget.settle("finished", 1, 60, proof)


@pytest.mark.parametrize("unlimited", [False, True])
def test_scheduler_overrun_is_accounted_and_finite_future_caps_still_apply(tmp_path, unlimited):
    path = tmp_path / "budget.json"
    budget = Budget(path, gpu_hours=None, cpu_hours=None if unlimited else 1 / 60)
    budget.reserve("cpu-finished", 0, 60)
    proof = {"job_id": "synthetic-terminal", "state": "TIMEOUT"}
    budget.settle("cpu-finished", 0, 87, proof)
    budget.settle("cpu-finished", 0, 87, proof)
    record = json.loads(path.read_text())["cpu-finished"]
    assert record["cpu_seconds"] == 120
    assert record["reservation"]["cpu_seconds"] == 60
    assert record["reservation_overrun"]["cpu_seconds"] == 60
    if unlimited:
        budget.reserve("next", 0, 60)
    else:
        with pytest.raises(ValueError, match="budget exhausted"):
            budget.reserve("next", 0, 60)
    with pytest.raises(ValueError, match="evidence changed"):
        budget.settle("cpu-finished", 0, 88, proof)


@pytest.mark.parametrize("invalid", [None, "active", "queue", "retry", "hardware", "duration"])
def test_only_proven_terminal_allocation_releases_hold(tmp_path, invalid):
    budget = Budget(tmp_path / "budget.json", **UNLIMITED)
    budget.reserve("campaign", 1, 600)
    root = Path(__file__).resolve().parents[1]
    runner = UnityRunner(root / "src/runners/unityhpc/connections/config.yaml", budget)
    resources = {"gpu_count": 1}
    attestation = {"verified": True, "job_id": "42", "requested": resources, "gpus": [{}]}
    if invalid == "hardware":
        attestation["verified"] = False
    hardware_file = tmp_path / "hardware.json"
    hardware_file.write_text(json.dumps(attestation))
    status = {
        "id": "campaign",
        "state": "failed",
        "spec": {"max_attempts": 1},
        "tasks": [
            {
                "attempt": 1,
                "job_id": "42",
                "spec": {"resources": resources},
                "observation": {
                    "job_id": "42",
                    "source": "ACCOUNTING",
                    "state": "FAILED",
                    "elapsed_seconds": 61,
                    "start": "2026-10-06T00:00:00",
                    "end": "2026-10-06T00:01:01",
                },
            }
        ],
    }
    if invalid == "active":
        status["state"] = "running"
    elif invalid == "queue":
        status["tasks"][0]["observation"]["source"] = "QUEUE"
    elif invalid == "retry":
        status["spec"]["max_attempts"] = 2
    elif invalid == "duration":
        status["tasks"][0]["observation"]["end"] = "Unknown"
    runner._settle_terminal(
        status,
        [
            {
                "path": ".controller/hardware-1.json",
                "sha256": "a" * 64,
                "verified_path": str(hardware_file),
            }
        ],
    )
    charged = json.loads(budget.path.read_text())["campaign"]["gpu_seconds"]
    assert charged == (120 if invalid is None else 600)


def test_uncertain_submission_keeps_reservation(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    budget = Budget(tmp_path / "budget.json", **UNLIMITED)
    runner = UnityRunner(root / "src/runners/unityhpc/connections/config.yaml", budget)
    monkeypatch.setattr(runner.client, "json", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    request = ExecutionRequest(
        "uncertain", tmp_path, ("python", "worker.py"), ("result.json",), CPU_HARDWARE, 300
    )
    with pytest.raises(TimeoutError):
        runner.submit(request)
    assert "uncertain" in json.loads(budget.path.read_text())


def test_definite_rejection_releases_unsubmitted_budget(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    budget = Budget(tmp_path / "budget.json", **UNLIMITED)
    runner = UnityRunner(root / "src/runners/unityhpc/connections/config.yaml", budget)
    monkeypatch.setattr(
        runner.client, "json", lambda *a: (_ for _ in ()).throw(ControllerError(400, "invalid"))
    )
    request = ExecutionRequest(
        "rejected", tmp_path, ("python", "worker.py"), ("result.json",), CPU_HARDWARE, 300
    )
    with pytest.raises(ControllerError):
        runner.submit(request)
    assert json.loads(budget.path.read_text()) == {}


@pytest.mark.parametrize("path", ["../escape", "/absolute", "."])
def test_artifact_paths_are_confined_before_network_access(tmp_path, path):
    receipt = {"path": path, "download_url": "/artifact", "size_bytes": 1, "sha256": "a" * 64}
    with pytest.raises(ValueError, match="path"):
        Client("http://127.0.0.1:1", 30, max_download_bytes=None).download(receipt, tmp_path)


def test_artifact_receipt_cannot_redirect_to_another_host(tmp_path):
    receipt = {
        "path": "result.json",
        "download_url": "http://another-host/result",
        "size_bytes": 1,
        "sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match="origin"):
        Client("http://127.0.0.1:1", 30, max_download_bytes=None).download(receipt, tmp_path)


@pytest.mark.parametrize("limit,expected", [(None, None), (2, 2 * 1024**3)])
def test_runner_honors_configured_artifact_download_limit(tmp_path, limit, expected):
    import yaml

    config = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1] / "src/runners/unityhpc/connections/config.yaml"
        ).read_text()
    )
    config["download_limit_gib"] = limit
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    backend = UnityRunner(path, Budget(tmp_path / "budget.json", **UNLIMITED))
    assert backend.client.max_download_bytes == expected


@pytest.mark.parametrize("unlimited", [False, True])
def test_large_artifact_respects_limit_and_still_requires_exact_stream(
    tmp_path, monkeypatch, unlimited
):
    import io
    from types import SimpleNamespace

    from runners.unityhpc import client

    calls = []

    def response(*args, **kwargs):
        calls.append(args)
        value = io.BytesIO(b"truncated artifact")
        value.url = "http://127.0.0.1:1/artifact"
        return value

    monkeypatch.setattr(client, "urlopen", response)
    monkeypatch.setattr(client.shutil, "disk_usage", lambda path: SimpleNamespace(free=8 * 1024**3))
    receipt = {
        "path": "evidence.tar.gz",
        "download_url": "/artifact",
        "size_bytes": 2 * 1024**3,
        "sha256": "a" * 64,
    }
    with pytest.raises(
        ValueError, match="digest or size mismatch" if unlimited else "download limit"
    ):
        Client(
            "http://127.0.0.1:1", 30, max_download_bytes=None if unlimited else 1024**3
        ).download(receipt, tmp_path)
    assert bool(calls) is unlimited
    assert not (tmp_path / "evidence.tar.gz").exists()
    assert not list(tmp_path.glob(".download-*"))


@pytest.mark.parametrize("qos", [None, "short"])
def test_controller_schema_copies_the_allocation(tmp_path, qos):
    root = Path(__file__).resolve().parents[1]
    runner = UnityRunner(
        root / "src/runners/unityhpc/connections/config.yaml",
        Budget(tmp_path / "budget.json", **UNLIMITED),
    )
    if qos:
        runner.config["qos"] = qos
    hardware = {
        "id": "unity-a100-v1",
        "gpu_count": 1,
        "gpu_name": "NVIDIA A100-SXM4-80GB",
        "gpu_memory_mb": 81920,
        "allocated_cpus": 8,
        "allocated_memory_mb": 65536,
    }
    request = ExecutionRequest(
        "comparison",
        tmp_path,
        ("bash", "worker.sh"),
        ("results.jsonl",),
        hardware,
        3600,
    )
    declaration = runner.declaration(request)
    assert "AGENTS.md" in declaration["bundle"]["exclude"]
    assert "CLAUDE.md" in declaration["bundle"]["exclude"]
    assert "artifacts" in declaration["bundle"]["exclude"]
    task = declaration["tasks"][0]
    assert task["model_class"] == hardware["id"]
    assert task["resources"]["expected_gpu_memory_mb"] == 81920
    assert task["resources"]["gpu_memory_tolerance_mb"] == 0
    assert task["resources"]["cpus"] == 8
    assert task["resources"]["memory_mb"] == 65536
    assert task["resources"].get("qos") == qos


def test_core_does_not_import_controller_or_scheduler_knowledge():
    root = Path(__file__).resolve().parents[1]
    sources = list((root / "src/agentic_eval").rglob("*.py"))
    for path in sources:
        source = path.read_text()
        assert "HPC_" not in source, path
        assert "SLURM_" not in source, path
        assert "runners.unityhpc" not in source, path
        assert "/api/v1/campaigns" not in source, path


def test_moved_connection_keeps_the_project_budget_ledger():
    root = Path(__file__).resolve().parents[1]
    runner = UnityRunner(root / "src/runners/unityhpc/connections/config.yaml")
    assert runner.budget.path == root / ".cache/budget.json"
