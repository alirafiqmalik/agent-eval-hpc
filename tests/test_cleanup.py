from pathlib import Path

from runners.unityhpc.jobs.cleanup_stale import orphan_fuse_pids, stale_runtime_pids


def _proc(
    root: Path, pid: int, parent: int, command: str, cgroup: str = "", comm: str = ""
) -> None:
    directory = root / str(pid)
    directory.mkdir()
    (directory / "status").write_text(f"PPid:\t{parent}\n")
    (directory / "cmdline").write_bytes(command.encode().replace(b" ", b"\0"))
    (directory / "cgroup").write_text(cgroup)
    (directory / "comm").write_text(comm)


def test_cleanup_only_selects_completed_trials_in_this_job(tmp_path):
    logs = tmp_path / "logs"
    completed = logs / "job/HumanEval_0"
    completed.mkdir(parents=True)
    (completed / "result.json").write_text("{}")
    (completed / "trial.log").write_text("Starting Singularity container with server on port 1234")
    active = logs / "job/HumanEval_1"
    active.mkdir()
    (active / "trial.log").write_text("Starting Singularity container with server on port 5678")

    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 101, 102, "python /staging/_hbexec.py --port 1234")
    _proc(proc, 102, 103, "appinit")
    _proc(proc, 103, 1, "Apptainer runtime parent: sandbox.sif", "job_7/step_batch")
    _proc(proc, 201, 202, "python /staging/_hbexec.py --port 5678")
    _proc(proc, 202, 203, "appinit")
    _proc(proc, 203, 1, "Apptainer runtime parent: sandbox.sif", "job_7/step_batch")
    _proc(proc, 301, 302, "python /staging/_hbexec.py --port 1234")
    _proc(proc, 302, 303, "appinit")
    _proc(proc, 303, 1, "Apptainer runtime parent: sandbox.sif", "job_8/step_batch")

    assert stale_runtime_pids("7", logs, proc) == [103]


def test_cleanup_only_selects_orphan_fuse_helpers_in_this_job(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 101, 1, "fuse", "job_7/step_batch", "squashfuse_ll")
    _proc(proc, 102, 1, "fuse", "job_7/step_batch", "fuse-overlayfs")
    _proc(proc, 103, 20, "fuse", "job_7/step_batch", "fuse-overlayfs")
    _proc(proc, 104, 1, "fuse", "job_8/step_batch", "squashfuse_ll")
    _proc(proc, 105, 1, "other", "job_7/step_batch", "other")

    assert orphan_fuse_pids("7", proc) == [101, 102]
