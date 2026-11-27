"""Run one Harbor job as a spawned Modal Function call.

Submit uploads a deterministic source bundle to a Modal Volume, records the
request in a Modal Dict, deploys the orchestrator app, and spawns one call. The
Dict entry makes submit idempotent; the Volume holds the bundle and outputs.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agentic_eval.jobs import repository_root, validate_job
from runners.budget import Budget
from runners.interface import (
    ExecutionConflictError,
    ExecutionNotFoundError,
    ExecutionRequest,
    request_digest,
)

ENTRYPOINT = "src/runners/modalcloud/jobs/task.py"
PROJECT_FILES = ("pyproject.toml", "uv.lock", ".python-version")
SKIPPED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "output"}
CONNECTION_KEYS = (
    "app_name",
    "runs_volume",
    "model_cache_volume",
    "registry",
    "max_attempts",
    "max_walltime_seconds",
    "allocation_limits",
)
SERVER_KEYS = (
    "image",
    "gpu",
    "gpu_count",
    "gpu_names",
    "gpu_memory_mb",
    "cpus",
    "memory_mb",
    "port",
    "context_length",
    "command",
)
TERMINAL = {"completed", "failed", "cancelled"}


def load_connection(path: Path) -> dict:
    config = yaml.safe_load(Path(path).read_text())
    if type(config) is not dict or any(key not in config for key in CONNECTION_KEYS):
        raise ValueError(f"{path} requires {', '.join(CONNECTION_KEYS)}")
    if type(config["max_attempts"]) is not int or not 1 <= config["max_attempts"] <= 2:
        raise ValueError("Modal max_attempts must be 1 or 2")
    return config


def load_runner_yaml(experiment: Path) -> dict:
    """Validate the Modal fields of runner.yaml and return it."""
    runner = yaml.safe_load((experiment / "runner.yaml").read_text())
    if type(runner) is not dict:
        raise ValueError("runner.yaml must be a mapping")
    hardware = runner.get("hardware")
    if type(hardware) is not dict or any(
        key not in hardware for key in ("id", "allocated_cpus", "allocated_memory_mb")
    ):
        raise ValueError("runner.yaml hardware requires id, allocated_cpus, allocated_memory_mb")
    walltime = runner.get("walltime_seconds")
    if type(walltime) is not int or walltime <= 0:
        raise ValueError("runner.yaml requires a positive walltime_seconds")
    server = runner.get("model_server")
    if type(server) is not dict or server.get("mode") not in ("modal", "none"):
        raise ValueError("Modal runner.yaml requires model_server.mode modal or none")
    if server["mode"] == "modal":
        if missing := [key for key in SERVER_KEYS if server.get(key) in (None, "")]:
            raise ValueError(f"model_server is missing {', '.join(missing)}")
        if type(server["gpu_count"]) is not int or server["gpu_count"] <= 0:
            raise ValueError("model_server.gpu_count must be a positive integer")
        if type(server["gpu_names"]) is not list:
            raise ValueError("model_server.gpu_names must list the accepted device names")
        placeholders = ("{model}", "{port}", "{context_length}")
        if any(token not in server["command"] for token in placeholders):
            raise ValueError("model_server.command must include {model}, {port}, {context_length}")
    return runner


def _bundle_files(root: Path, experiment: Path) -> list[Path]:
    files = [root / name for name in PROJECT_FILES if (root / name).is_file()]
    for base in (root / "src", experiment):
        for path in base.rglob("*"):
            relative = path.relative_to(base).parts
            if path.is_symlink() or not path.is_file() or SKIPPED_PARTS & set(relative):
                continue
            if path.suffix in (".pyc", ".sif"):
                continue
            files.append(path)
    return sorted(set(files))


def build_bundle(root: Path, job: Path) -> tuple[bytes, str, int]:
    """Return a byte-stable tar.gz of the project files and the job's experiment."""
    experiment = job.resolve().parent
    config = yaml.safe_load(job.read_text())
    for section in ("datasets", "tasks"):
        for item in config.get(section) or []:
            if isinstance(item, dict) and item.get("path"):
                path = (root / item["path"]).resolve()
                if not path.is_relative_to(experiment):
                    raise ValueError(f"Modal bundles hold only the experiment folder: {path}")
    files = _bundle_files(root, experiment)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            data = path.read_bytes()
            info.size = len(data)
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            archive.addfile(info, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as stream:
        stream.write(raw.getvalue())
    payload = compressed.getvalue()
    return payload, hashlib.sha256(payload).hexdigest(), len(files)


class ModalAPI:
    """The Modal SDK calls the adapter needs, kept apart so tests can replace them."""

    def __init__(self, config: dict):
        import modal

        self.modal = modal
        self.config = config
        self.volume = modal.Volume.from_name(config["runs_volume"], create_if_missing=True)
        self.registry = modal.Dict.from_name(config["registry"], create_if_missing=True)

    def get(self, identity: str) -> dict | None:
        return self.registry.get(identity)

    def claim(self, identity: str, record: dict) -> bool:
        return self.registry.put(identity, record, skip_if_exists=True)

    def put(self, identity: str, record: dict) -> None:
        self.registry.put(identity, record)

    def upload(self, files: dict[str, bytes]) -> None:
        with self.volume.batch_upload(force=True) as batch:
            for path, data in files.items():
                batch.put_file(io.BytesIO(data), path)

    def read(self, path: str) -> bytes | None:
        try:
            return b"".join(self.volume.read_file(path))
        except (FileNotFoundError, self.modal.exception.NotFoundError):
            return None

    def deploy(self) -> None:
        from runners.modalcloud.app import app

        app.deploy(name=self.config["app_name"])

    def spawn(self, options: dict, arguments: dict) -> str:
        function = self.modal.Function.from_name(self.config["app_name"], "orchestrate")
        options = {**options, "volumes": {"/runs": self.volume}}
        return function.with_options(**options).spawn(**arguments).object_id

    def call_state(self, call_id: str) -> str:
        errors = self.modal.exception
        try:
            result = self.modal.FunctionCall.from_id(call_id).get(timeout=0)
        except (errors.FunctionTimeoutError, errors.OutputExpiredError):
            return "failed"
        except TimeoutError:
            return "running"
        except Exception:
            return "failed"
        return "completed" if result.get("exit_code") == 0 else "failed"

    def cancel(self, call_id: str, tags: dict) -> int:
        self.modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)
        app = self.modal.App.lookup(self.config["app_name"], create_if_missing=True)
        sandboxes = list(self.modal.Sandbox.list(app_id=app.app_id, tags=tags))
        for sandbox in sandboxes:
            sandbox.terminate()
        return len(sandboxes)


class ModalRunner:
    # The Dict claim compares the request digest and the source bundle digest.
    # An existing identity returns its run or rejects changed inputs.
    idempotent_submit = True

    def __init__(self, config: Path, budget: Budget | None = None, api: ModalAPI | None = None):
        self.config = load_connection(config)
        self._api = api
        limits = self.config["allocation_limits"]
        project = next(
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / "pyproject.toml").is_file()
        )
        self.budget = budget or Budget(
            project / ".cache/budget.json",
            gpu_hours=limits["gpu_hours"],
            cpu_hours=limits["cpu_hours"],
        )

    @property
    def api(self) -> ModalAPI:
        if self._api is None:
            self._api = ModalAPI(self.config)
        return self._api

    def request_for(
        self, root: Path, job: Path, run_id: str, walltime_seconds: int | None = None
    ) -> ExecutionRequest:
        root = root.resolve()
        if repository_root(job.resolve()) != root:
            raise ValueError("job file is outside the repository")
        validate_job(job)
        runner = load_runner_yaml(job.resolve().parent)
        server = runner["model_server"]
        hardware = {
            **runner["hardware"],
            "gpu_count": server["gpu_count"] if server["mode"] == "modal" else 0,
            "gpu_type": server.get("gpu") if server["mode"] == "modal" else None,
        }
        return ExecutionRequest(
            run_id,
            root,
            ("python", ENTRYPOINT, job.resolve().relative_to(root).as_posix()),
            ("harbor-jobs.tar.gz",),
            hardware,
            walltime_seconds or runner["walltime_seconds"],
            self.config["max_attempts"],
            {
                "EVAL_MODAL_APP": self.config["app_name"],
                "EVAL_MODAL_MODEL_CACHE": self.config["model_cache_volume"],
            },
        )

    def _job(self, request: ExecutionRequest) -> Path:
        return request.bundle / request.argv[2]

    def plan(self, request: ExecutionRequest) -> dict:
        from runners.modalcloud.app import GRACE_SECONDS

        if request.walltime_seconds > self.config["max_walltime_seconds"]:
            raise ValueError(
                f"walltime exceeds the Modal limit of {self.config['max_walltime_seconds']}s"
            )
        _, source, count = build_bundle(request.bundle, self._job(request))
        hardware = request.hardware
        return {
            "app": self.config["app_name"],
            "function": "orchestrate",
            "options": {
                "cpu": float(hardware["allocated_cpus"]),
                "memory": hardware["allocated_memory_mb"],
                "timeout": request.walltime_seconds + GRACE_SECONDS,
                "retries": request.max_attempts - 1,
            },
            "arguments": {
                "run_id": request.id,
                "argv": list(request.argv),
                "outputs": list(request.outputs),
                "environment": dict(request.environment),
                "walltime_seconds": request.walltime_seconds,
                "runs_volume": self.config["runs_volume"],
            },
            "hardware": hardware,
            "bundle": {"sha256": source, "files": count},
        }

    def _record(self, identity: str) -> dict:
        record = self.api.get(identity)
        if record is None:
            raise ExecutionNotFoundError(identity)
        return record

    def submit(self, request: ExecutionRequest) -> dict:
        declaration = self.plan(request)
        digest = request_digest(request)
        source = declaration["bundle"]["sha256"]
        record = {
            "id": request.id,
            "request_sha256": digest,
            "source_sha256": source,
            "declaration": declaration,
            "submitted_at": datetime.now(UTC).isoformat(),
        }
        existing = self.api.get(request.id)
        if existing is None:
            self.budget.reserve(
                request.id,
                request.hardware["gpu_count"],
                request.walltime_seconds,
                request.max_attempts,
            )
            payload, _, _ = build_bundle(request.bundle, self._job(request))
            self.api.upload(
                {
                    f"runs/{request.id}/bundle.tar.gz": payload,
                    f"runs/{request.id}/request.json": json.dumps(record, indent=2).encode(),
                }
            )
            if not self.api.claim(request.id, record):
                existing = self.api.get(request.id)
        if existing is not None:
            if (existing["request_sha256"], existing["source_sha256"]) != (digest, source):
                raise ExecutionConflictError(f"{request.id} exists with a different request")
            record = existing
        if not record.get("call_id"):
            # A claim without a call is an interrupted submit. Finish it.
            self.api.deploy()
            record["call_id"] = self.api.spawn(declaration["options"], declaration["arguments"])
            self.api.put(request.id, record)
        return self.status(request.id)

    def _exit(self, identity: str) -> dict | None:
        raw = self.api.read(f"runs/{identity}/output/exit.json")
        return json.loads(raw) if raw else None

    def status(self, identity: str) -> dict:
        record = self._record(identity)
        exit_record = self._exit(identity)
        if exit_record is not None:
            state = "completed" if exit_record["exit_code"] == 0 else "failed"
        elif not record.get("call_id"):
            state = "queued"
        else:
            state = self.api.call_state(record["call_id"])
        if record.get("cancelled") and state != "completed":
            state = "cancelled"
        return {
            "id": identity,
            "state": state,
            "request_hash": record["request_sha256"],
            "spec": {
                "bundle_manifest": {"sha256": record["source_sha256"]},
                "max_attempts": record["declaration"]["options"]["retries"] + 1,
                "declaration": record["declaration"],
            },
            "tasks": [
                {
                    "task_id": "evaluation",
                    "state": state,
                    "job_id": record.get("call_id"),
                    "exit": exit_record,
                }
            ],
        }

    def attest(self, request: ExecutionRequest, observation: dict) -> dict:
        """Match the recorded request digest and the locally rebuilt source bundle."""
        _, source, _ = build_bundle(request.bundle, self._job(request))
        digest = request_digest(request)
        recorded = observation.get("spec", {}).get("bundle_manifest", {}).get("sha256")
        matched = (
            observation.get("id") == request.id
            and observation.get("request_hash") == digest
            and recorded == source
        )
        return {
            "matched": bool(matched),
            "request_sha256": digest,
            "controller_request_sha256": observation.get("request_hash"),
            "controller_source_sha256": recorded,
            "source": "modal-dict-record-and-bundle-sha256",
        }

    def cancel(self, identity: str) -> dict:
        from runners.modalcloud.environment import RUN_TAG

        record = self._record(identity)
        record["cancelled"] = True
        self.api.put(identity, record)
        if record.get("call_id"):
            self.api.cancel(record["call_id"], {RUN_TAG: identity})
        return self.status(identity)

    def fetch(self, identity: str, destination: Path) -> list[dict]:
        status = self.status(identity)
        exit_record = status["tasks"][0]["exit"]
        if exit_record is None:
            return []
        receipts = []
        target = destination / "evaluation"
        for name, expected in sorted(exit_record["files"].items()):
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe artifact path: {name}")
            data = self.api.read(f"runs/{identity}/output/{name}")
            if data is None or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"artifact {name} is missing or differs from its exit record")
            local = target / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            receipts.append(
                {"path": name, "sha256": expected, "bytes": len(data), "verified_path": str(local)}
            )
        self._settle(status, exit_record)
        return receipts

    def _settle(self, status: dict, exit_record: dict) -> None:
        identity = status["id"]
        if (
            status["state"] not in TERMINAL
            or status["spec"]["max_attempts"] != 1
            or not self.budget.path.exists()
            or identity not in json.loads(self.budget.path.read_text())
        ):
            return
        gpu_count = status["spec"]["declaration"]["hardware"]["gpu_count"]
        elapsed = exit_record.get("elapsed_seconds")
        if not isinstance(elapsed, int) or elapsed < 0 or not math.isfinite(elapsed):
            return
        evidence = {
            "backend": "modal",
            "function_call_id": exit_record.get("function_call_id"),
            "started_at": exit_record["started_at"],
            "finished_at": exit_record["finished_at"],
        }
        self.budget.settle(identity, gpu_count, elapsed, evidence)
