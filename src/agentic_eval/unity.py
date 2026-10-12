"""Turn one Harbor job file into a Unity execution request."""

from __future__ import annotations

from pathlib import Path

import yaml

from agentic_eval.jobs import repository_root, validate_job
from runners.interface import ExecutionRequest

HARDWARE_KEYS = ("id", "gpu_count", "allocated_cpus", "allocated_memory_mb")
GPU_KEYS = ("gpu_name", "gpu_memory_mb")


def load_runner_request(job_path: Path, runner_config: Path) -> tuple[dict, int, int]:
    """Read this experiment's hardware from runner.yaml and retries from the connection."""
    experiment = yaml.safe_load((job_path.parent / "runner.yaml").read_text())
    if type(experiment) is not dict:
        raise ValueError("runner.yaml must be a mapping")
    hardware = experiment.get("hardware")
    if type(hardware) is not dict or any(key not in hardware for key in HARDWARE_KEYS):
        raise ValueError("runner.yaml requires a hardware mapping")
    if hardware["gpu_count"] and any(hardware.get(key) in (None, "") for key in GPU_KEYS):
        raise ValueError("a GPU allocation requires gpu_name and gpu_memory_mb")
    walltime = experiment.get("walltime_seconds")
    server = experiment.get("model_server")
    if type(walltime) is not int or walltime <= 0:
        raise ValueError("runner.yaml requires a positive walltime_seconds")
    if type(server) is not dict:
        raise ValueError("runner.yaml requires a model_server mapping")
    port = server.get("port")
    context = server.get("context_length")
    command = server.get("command")
    if type(port) is not int or port <= 0 or type(context) is not int or context <= 0:
        raise ValueError("model_server requires a positive port and context_length")
    placeholders = ("{model}", "{port}", "{context_length}")
    if type(command) is not str or any(token not in command for token in placeholders):
        raise ValueError("model_server.command must include {model}, {port}, and {context_length}")
    connection = yaml.safe_load(runner_config.read_text())
    attempts = connection.get("max_attempts") if type(connection) is dict else None
    if type(attempts) is not int:
        raise ValueError("runner connection requires an integer attempt count")
    return hardware, walltime, attempts


def request_for(
    root: Path,
    job_path: Path,
    run_id: str,
    runner_config: Path,
    walltime_seconds: int | None = None,
) -> ExecutionRequest:
    """Package the repository and ask the cluster to run this job with Harbor."""
    root = root.resolve()
    if repository_root(job_path.resolve()) != root:
        raise ValueError("job file is outside the repository")
    validate_job(job_path)
    hardware, walltime, attempts = load_runner_request(job_path, runner_config)
    if walltime_seconds is not None:
        walltime = walltime_seconds
    relative = job_path.resolve().relative_to(root).as_posix()
    return ExecutionRequest(
        run_id,
        root,
        ("bash", "src/runners/unityhpc/jobs/task.sh", relative),
        ("harbor-jobs.tar.gz",),
        hardware,
        walltime,
        attempts,
    )
