"""Check a Harbor job file against the tasks in this repository."""

from __future__ import annotations

from pathlib import Path

import yaml
from harbor.models.job.config import JobConfig

TASK_PARTS = ("instruction.md", "task.toml", "environment", "tests")


def repository_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "experiments").is_dir():
            return candidate
    raise ValueError("job file is outside the repository")


def load_job(path: Path) -> JobConfig:
    data = yaml.safe_load(path.read_text())
    if type(data) is not dict:
        raise ValueError("job config must be a mapping")
    return JobConfig.model_validate(data)


def _task_dir(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _require_task(path: Path) -> None:
    missing = [part for part in TASK_PARTS if not (path / part).exists()]
    if missing:
        raise ValueError(f"{path} is missing {', '.join(missing)}")


EXPERIMENT_FILES = (
    "runner.yaml",
    "agent/agent-config.yaml",
    "agent/prompts/system.md",
    "sandbox/sandbox.def",
    "tools/verifier/test.sh",
    "tools/verifier/grade.py",
    "tools/trajectory/export.py",
    "tools/eval/summarize.py",
)


def validate_experiment(path: Path) -> JobConfig:
    """Load the Harbor job and require the experiment files around it."""
    job = validate_job(path)
    experiment = path.resolve().parent
    missing = [name for name in EXPERIMENT_FILES if not (experiment / name).is_file()]
    if missing:
        raise ValueError(f"{experiment} is missing {', '.join(missing)}")
    return job


def validate_job(path: Path) -> JobConfig:
    """Load the job and require every named local task to be present."""
    root = repository_root(path.resolve())
    job = load_job(path)
    if not job.datasets and not job.tasks:
        raise ValueError("job config names no datasets or tasks")
    for dataset in job.datasets:
        if dataset.path is None:
            continue
        base = _task_dir(root, dataset.path)
        if not base.is_dir():
            raise ValueError(f"dataset path does not exist: {base}")
        if dataset.task_names:
            names = dataset.task_names
        else:
            names = sorted(
                child.name for child in base.iterdir() if (child / "task.toml").is_file()
            )
        if not names:
            raise ValueError(f"dataset contains no tasks: {base}")
        for name in names:
            _require_task(base / name)
    for task in job.tasks:
        if task.path is None:
            continue
        _require_task(_task_dir(root, task.path))
    return job
