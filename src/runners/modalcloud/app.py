"""Modal app with one Function that runs a submitted bundle's command.

The adapter deploys this app, then spawns one call per run with that run's
resources, timeout, and volume. The call reads its bundle from the runs volume
and writes its declared outputs and exit record back to the same volume.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[3]
PROJECT = "/opt/agentic-eval"
RUNS = Path("/runs")
REPO = Path("/root/repo")
OUTPUT = Path("/root/output")
# Time after the command's walltime to archive and commit its outputs.
GRACE_SECONDS = 600

# Dependencies come from the locked project. The bundle brings the source.
# Modal's builder sets UV_INDEX_URL to its PyPI mirror, which differs from the
# lock's index, so the locked sync clears it and installs the locked sources.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("uv==0.11.2")
    .add_local_file(ROOT / "pyproject.toml", f"{PROJECT}/pyproject.toml", copy=True)
    .add_local_file(ROOT / "uv.lock", f"{PROJECT}/uv.lock", copy=True)
    .run_commands(
        f"cd {PROJECT} && env -u UV_INDEX_URL UV_PYTHON=/usr/local/bin/python3 "
        "UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --no-install-project"
    )
    .env({"PATH": f"{PROJECT}/.venv/bin:/usr/local/bin:/usr/bin:/bin"})
    .add_local_python_source("runners")
)
app = modal.App("agentic-eval", image=image)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _collect(outputs: list[str], destination: Path) -> tuple[dict[str, str], list[str]]:
    """Copy declared outputs and backend evidence; return their digests and the missing."""
    files, missing = {}, []
    names = list(outputs)
    controller = OUTPUT / ".controller"
    if controller.is_dir():
        names += [p.relative_to(OUTPUT).as_posix() for p in controller.rglob("*") if p.is_file()]
    for name in names:
        source = OUTPUT / name
        if not source.is_file():
            missing.append(name)
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[name] = _sha256(target)
    return files, missing


@app.function(timeout=GRACE_SECONDS)
def orchestrate(
    run_id: str,
    argv: list[str],
    outputs: list[str],
    environment: dict[str, str],
    walltime_seconds: int,
    runs_volume: str,
) -> dict:
    run = RUNS / "runs" / run_id
    volume = modal.Volume.from_name(runs_volume)
    # A warm container keeps the volume state from its first mount. Load the new bundle.
    volume.reload()
    started = time.time()
    shutil.rmtree(REPO, ignore_errors=True)
    shutil.rmtree(OUTPUT, ignore_errors=True)
    OUTPUT.mkdir(parents=True)
    with tarfile.open(run / "bundle.tar.gz") as bundle:
        bundle.extractall(REPO, filter="data")
    env = {
        **os.environ,
        **environment,
        "EVAL_RUN_ID": run_id,
        "EVAL_OUTPUT_DIR": str(OUTPUT),
        "PYTHONPATH": str(REPO / "src"),
    }
    try:
        exit_code = subprocess.run(argv, cwd=REPO, env=env, timeout=walltime_seconds).returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
    destination = run / "output"
    shutil.rmtree(destination, ignore_errors=True)
    files, missing = _collect(outputs, destination)
    finished = time.time()
    record = {
        "exit_code": exit_code,
        "timed_out": exit_code == 124,
        "started_at": started,
        "finished_at": finished,
        "elapsed_seconds": round(finished - started),
        "files": files,
        "missing": missing,
        "function_call_id": modal.current_function_call_id(),
    }
    (destination / "exit.json").write_text(json.dumps(record, indent=2) + "\n")
    volume.commit()
    return record
