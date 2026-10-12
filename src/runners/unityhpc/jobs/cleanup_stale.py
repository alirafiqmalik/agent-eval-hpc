"""Reap orphaned Apptainer services from completed trials in one Slurm job."""

from __future__ import annotations

import argparse
import os
import re
import signal
import time
from pathlib import Path


def _proc_text(proc_root: Path, pid: int, name: str) -> str:
    try:
        return (
            (proc_root / str(pid) / name).read_bytes().replace(b"\0", b" ").decode(errors="replace")
        )
    except (OSError, ValueError):
        return ""


def _parent(proc_root: Path, pid: int) -> int | None:
    match = re.search(r"(?m)^PPid:\s*(\d+)", _proc_text(proc_root, pid, "status"))
    return int(match.group(1)) if match else None


def stale_runtime_pids(job_id: str, logs_dir: Path, proc_root: Path = Path("/proc")) -> list[int]:
    """Match completed trial ports to orphaned runtimes in this job's cgroup."""
    if not job_id.isdigit():
        raise ValueError("Slurm job id must be numeric")
    completed_ports = set()
    for result in logs_dir.glob("*/*/result.json"):
        trial_log = result.with_name("trial.log")
        if trial_log.is_file():
            completed_ports.update(re.findall(r"server on port (\d+)", trial_log.read_text()))

    runtimes = set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        service_pid = int(entry.name)
        command = _proc_text(proc_root, service_pid, "cmdline")
        if "/staging/_hbexec.py" not in command:
            continue
        port = re.search(r"--port (\d+)", command)
        if not port or port.group(1) not in completed_ports:
            continue
        appinit = _parent(proc_root, service_pid)
        runtime = _parent(proc_root, appinit) if appinit else None
        if runtime is None or _parent(proc_root, runtime) != 1:
            continue
        if "Apptainer runtime parent: sandbox.sif" not in _proc_text(proc_root, runtime, "cmdline"):
            continue
        if f"job_{job_id}/" not in _proc_text(proc_root, runtime, "cgroup"):
            continue
        runtimes.add(runtime)
    return sorted(runtimes)


def orphan_fuse_pids(job_id: str, proc_root: Path = Path("/proc")) -> list[int]:
    """Find FUSE helpers left behind after their Apptainer runtime exited."""
    if not job_id.isdigit():
        raise ValueError("Slurm job id must be numeric")
    helpers = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if _parent(proc_root, pid) != 1:
            continue
        if _proc_text(proc_root, pid, "comm").strip() not in {"squashfuse_ll", "fuse-overlayfs"}:
            continue
        if f"job_{job_id}/" not in _proc_text(proc_root, pid, "cgroup"):
            continue
        helpers.append(pid)
    return sorted(helpers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("logs_dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--interval", type=int, default=0)
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("interval must be nonnegative")
    while True:
        runtimes = stale_runtime_pids(args.job_id, args.logs_dir)
        helpers = orphan_fuse_pids(args.job_id)
        pids = sorted(set(runtimes + helpers))
        if args.apply:
            for pid in runtimes:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for pid in helpers:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if pids:
            action = "reaped" if args.apply else "found"
            print(
                f"{action} {len(runtimes)} stale Apptainer runtimes and "
                f"{len(helpers)} orphan FUSE helpers",
                flush=True,
            )
        if args.interval == 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
