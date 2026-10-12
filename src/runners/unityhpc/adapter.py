"""Translate portable execution requests into the existing campaign contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from runners.budget import Budget
from runners.interface import (
    ExecutionConflictError,
    ExecutionNotFoundError,
    ExecutionRequest,
    request_digest,
)
from runners.unityhpc.client import Client, ControllerError


class UnityRunner:
    # The controller compares the spec and source digest under its campaign lock.
    # An existing identity returns the same campaign or rejects changed inputs.
    idempotent_submit = True
    # The controller inventories every non-excluded file in the bundle. Keep
    # local caches, generated results, and the guide symlinks out of uploads.
    BUNDLE_EXCLUDES = [
        ".cache",
        ".cursor",
        ".ruff_cache",
        "artifacts",
        "experiments/*/output",
        "experiments/*/sandbox/*.sif",
        "AGENTS.md",
        "CLAUDE.md",
    ]

    def __init__(self, config: Path, budget: Budget | None = None):
        self.config = yaml.safe_load(config.read_text())
        for key, variable in (
            ("controller_url", "UNITYHPC_CONTROLLER_URL"),
            ("runtime_root", "UNITYHPC_RUNTIME_ROOT"),
        ):
            if value := os.environ.get(variable):
                self.config[key] = value
        if not Path(self.config["runtime_root"]).is_absolute():
            raise ValueError("Unity runtime_root must be an absolute path")
        download_limit = self.config["download_limit_gib"]
        if download_limit is not None and (
            type(download_limit) not in (int, float)
            or not math.isfinite(download_limit)
            or download_limit < 0
        ):
            raise ValueError("download limit must be nonnegative GiB or null")
        self.client = Client(
            self.config["controller_url"],
            self.config["timeout_seconds"],
            max_download_bytes=None if download_limit is None else int(download_limit * 1024**3),
        )
        limits = self.config["allocation_limits"]
        project = next(
            (
                parent
                for parent in Path(__file__).resolve().parents
                if (parent / "pyproject.toml").is_file()
            ),
            Path.cwd().resolve(),
        )
        self.budget = budget or Budget(
            project / ".cache/budget.json",
            gpu_hours=limits["gpu_hours"],
            cpu_hours=limits["cpu_hours"],
        )

    def declaration(self, request: ExecutionRequest) -> dict:
        hw = request.hardware
        download_limit = self.client.max_download_bytes
        resources = {
            "gpu_count": hw["gpu_count"],
            "cpus": hw["allocated_cpus"],
            "memory_mb": hw["allocated_memory_mb"],
            "walltime_seconds": request.walltime_seconds,
            "partitions": self.config["partitions" if hw["gpu_count"] else "cpu_partitions"],
        }
        if hw["gpu_count"]:
            resources.update(
                gpu_type=self.config["gpu_type"],
                constraint=self.config["constraint"],
                expected_gpu_name=hw["gpu_name"],
                expected_gpu_memory_mb=hw["gpu_memory_mb"],
                gpu_memory_tolerance_mb=hw.get("gpu_memory_tolerance_mb", 0),
            )
        if self.config.get("nodelist"):
            resources["nodelist"] = self.config["nodelist"]
        if self.config.get("qos"):
            resources["qos"] = self.config["qos"]
        task = {
            "task_id": "evaluation",
            "model_class": hw["id"],
            "argv": list(request.argv),
            "env": {
                "EVAL_RUNTIME_ROOT": self.config["runtime_root"],
                "EVAL_ALLOCATED_CPUS": str(hw["allocated_cpus"]),
                "EVAL_ALLOCATED_MEMORY_MB": str(hw["allocated_memory_mb"]),
                "EVAL_DOWNLOAD_LIMIT_BYTES": (
                    "unlimited" if download_limit is None else str(download_limit)
                ),
                **request.environment,
            },
            "resources": resources,
            "outputs": list(request.outputs),
            "depends_on": [],
        }
        if any(k.startswith("HPC_") for k in task["env"]):
            raise ValueError("controller-owned environment cannot be overridden")
        return {
            "schema_version": 1,
            "campaign_id": request.id,
            "bundle": {
                "path": str(request.bundle.resolve()),
                "exclude": self.BUNDLE_EXCLUDES,
            },
            "max_concurrent": 1,
            "max_attempts": request.max_attempts,
            "tasks": [task],
        }

    def plan(self, request: ExecutionRequest) -> dict:
        return self.client.json("POST", "/api/v1/campaigns/plan", self.declaration(request))

    def submit(self, request: ExecutionRequest) -> dict:
        self.budget.reserve(
            request.id,
            request.hardware["gpu_count"],
            request.walltime_seconds,
            request.max_attempts,
        )
        try:
            return self.client.json("POST", "/api/v1/campaigns", self.declaration(request))[
                "campaign"
            ]
        except ControllerError as exc:
            if exc.status in (400, 403, 404, 422):
                self.budget.release_unsubmitted(request.id)
            if exc.status == 409:
                raise ExecutionConflictError(str(exc)) from exc
            raise

    def status(self, identity: str) -> dict:
        try:
            return self.client.json("GET", f"/api/v1/campaigns/{identity}")["campaign"]
        except ControllerError as error:
            if error.status == 404:
                raise ExecutionNotFoundError(identity) from error
            raise

    def cancel(self, identity: str) -> dict:
        response = self.client.json("POST", f"/api/v1/campaigns/{identity}/cancel", {})
        return response.get("campaign", response)

    def attest(self, request: ExecutionRequest, observation: dict) -> dict:
        """Match controller-normalized controls and the complete source inventory."""
        expected = self.declaration(request)
        # The portable controller fills this default even for CPU allocations.
        for task in expected["tasks"]:
            task["resources"].setdefault("constraint", "")
        plan = self.plan(request)
        source = plan.get("bundle", {}).get("sha256")
        source_valid = type(source) is str and re.fullmatch(r"[a-f0-9]{64}", source) is not None
        spec = observation.get("spec")
        normalized = (
            {k: v for k, v in spec.items() if k not in {"backend", "bundle_manifest"}}
            if type(spec) is dict
            else None
        )
        request_hash = hashlib.sha256(
            json.dumps(
                {"spec": expected, "source_sha256": source},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        matched = (
            observation.get("id") == request.id
            and normalized == expected
            and source_valid
            and spec.get("bundle_manifest", {}).get("sha256") == source
            and observation.get("request_hash") == request_hash
        )
        return {
            "matched": bool(matched),
            "request_sha256": request_digest(request),
            "controller_request_sha256": request_hash,
            "controller_source_sha256": source,
            "source": "controller-normalized-request-and-source-inventory",
        }

    def fetch(self, identity: str, destination: Path) -> list[dict]:
        status = self.status(identity)
        receipts = []
        for task in status.get("tasks", []):
            artifacts = task.get("artifacts") or {}
            for receipt in [*artifacts.get("files", []), *artifacts.get("controller_files", [])]:
                receipts.append(self.client.download(receipt, destination / task["task_id"]))
        self._settle_terminal(status, receipts)
        return receipts

    def _settle_terminal(self, status: dict, receipts: list[dict]) -> None:
        if not self.budget.path.exists() or status["id"] not in json.loads(
            self.budget.path.read_text()
        ):
            return
        # Multiple attempts need separate accounting records. Keep their full hold.
        if (
            status.get("state") not in ("completed", "failed", "cancelled")
            or status.get("spec", {}).get("max_attempts") != 1
            or len(status.get("tasks", [])) != 1
        ):
            return
        task = status["tasks"][0]
        observation = task.get("observation") or {}
        terminal = {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "TIMEOUT",
            "OUT_OF_MEMORY",
            "NODE_FAIL",
            "PREEMPTED",
        }
        if (
            task.get("attempt") != 1
            or observation.get("source") != "ACCOUNTING"
            or observation.get("state") not in terminal
            or observation.get("job_id") != task.get("job_id")
        ):
            return
        elapsed = observation.get("elapsed_seconds")
        if not isinstance(elapsed, int) or elapsed < 0:
            return
        try:
            duration = (
                datetime.fromisoformat(observation["end"])
                - datetime.fromisoformat(observation["start"])
            ).total_seconds()
        except (ValueError, KeyError, TypeError):
            return
        if abs(duration - elapsed) > 2:
            return
        receipt = next((r for r in receipts if r["path"] == ".controller/hardware-1.json"), None)
        if receipt is None:
            return
        attestation = json.loads(Path(receipt["verified_path"]).read_text())
        resources = task["spec"]["resources"]
        if (
            attestation.get("verified") is not True
            or attestation.get("job_id") != task["job_id"]
            or attestation.get("requested") != resources
            or len(attestation.get("gpus", [])) != resources["gpu_count"]
        ):
            return
        evidence = {
            "backend": "unityhpc",
            "job_id": task["job_id"],
            "state": observation["state"],
            "start": observation["start"],
            "end": observation["end"],
            "hardware_receipt_sha256": receipt["sha256"],
        }
        self.budget.settle(status["id"], resources["gpu_count"], elapsed, evidence)
