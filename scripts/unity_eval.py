"""Submit or resume a Unity campaign, then collect and verify its evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

from agentic_eval.jobs import validate_experiment
from agentic_eval.launch import post_run, unpack_run
from agentic_eval.unity import request_for
from runners.discovery import load_runner
from runners.interface import ExecutionNotFoundError
from runners.unityhpc.client import ControllerError

TERMINAL = {"completed", "failed", "cancelled"}


def _transient(error: Exception) -> bool:
    if isinstance(error, ControllerError):
        return error.status == 429 or error.status >= 500
    return isinstance(error, (URLError, TimeoutError))


def wait_for_terminal(runner, run_id: str, poll_seconds: int) -> dict:
    """Poll by campaign ID so the script can be restarted without losing state."""
    last_state = None
    last_report = 0.0
    failures = 0
    while True:
        try:
            campaign = runner.status(run_id)
            failures = 0
        except (ControllerError, URLError, TimeoutError) as error:
            if not _transient(error):
                raise
            failures += 1
            if failures > 20:
                raise
            print(f"{run_id}: controller unavailable ({failures}/20): {error}", flush=True)
            time.sleep(poll_seconds)
            continue
        state = campaign["state"]
        tasks = campaign.get("tasks", [])
        task_states = ",".join(task.get("state", "?") for task in tasks)
        current = (state, task_states)
        now = time.monotonic()
        if current != last_state or now - last_report >= 300:
            print(f"{run_id}: campaign={state} tasks={task_states}", flush=True)
            last_state = current
            last_report = now
        if state in TERMINAL:
            return campaign
        time.sleep(poll_seconds)


def submit_with_retries(runner, request) -> None:
    """Repeat an uncertain submit using the controller's idempotent run ID."""
    for attempt in range(1, 6):
        try:
            runner.submit(request)
            return
        except (ControllerError, URLError, TimeoutError) as error:
            if not _transient(error) or attempt == 5:
                raise
            print(f"{request.id}: submission retry {attempt}/5: {error}", flush=True)
            time.sleep(30)


def collect(runner, job: Path, run_id: str, output: Path, campaign: dict) -> dict:
    """Fetch verified controller artifacts and require every Harbor trial to be scored."""
    output.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 6):
        try:
            receipts = runner.fetch(run_id, output)
            break
        except (ControllerError, URLError, TimeoutError) as error:
            if not _transient(error) or attempt == 5:
                raise
            print(f"{run_id}: artifact fetch retry {attempt}/5: {error}", flush=True)
            time.sleep(30)
    if not any(receipt.get("path", "").endswith("harbor-jobs.tar.gz") for receipt in receipts):
        raise ValueError("campaign has no Harbor results archive")
    unpack_run(output)
    post_run(job.resolve().parent, output)

    summary_path = output / "results/summary.json"
    summary = json.loads(summary_path.read_text())
    hardware_paths = list(output.glob("*/.controller/hardware-*.json"))
    hardware_verified = bool(hardware_paths) and all(
        json.loads(path.read_text()).get("verified") is True for path in hardware_paths
    )
    tasks_completed = bool(campaign.get("tasks")) and all(
        task.get("state") == "completed" for task in campaign["tasks"]
    )
    complete = (
        campaign.get("state") == "completed"
        and tasks_completed
        and hardware_verified
        and summary.get("complete") is True
        and isinstance(summary.get("expected_total"), int)
        and summary["expected_total"] > 0
        and summary.get("expected_total") == summary.get("total")
    )
    manifest = {
        "run_id": run_id,
        "job": str(job.resolve()),
        "campaign_state": campaign.get("state"),
        "source_sha256": campaign.get("spec", {}).get("bundle_manifest", {}).get("sha256"),
        "request_sha256": campaign.get("request_hash"),
        "slurm_job_ids": [task.get("job_id") for task in campaign.get("tasks", [])],
        "hardware_verified": hardware_verified,
        "artifacts": receipts,
        "summary": {
            key: summary.get(key) for key in ("passed", "total", "expected_total", "errors")
        },
        "complete": complete,
        "collected_at": datetime.now(UTC).isoformat(),
    }
    manifest_path = output / "results/evaluation-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"manifest": str(manifest_path), **manifest["summary"], "complete": complete}))
    if not complete:
        raise ValueError(f"incomplete evaluation; inspect {manifest_path}")
    return manifest


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Run scripts/setup.sh once. For another Unity account, set "
            "UNITYHPC_CONTROLLER_URL and UNITYHPC_RUNTIME_ROOT to its controller "
            "and prepared runtime before running this command."
        ),
    )
    actions = command.add_subparsers(dest="action", required=True)
    for name in ("run", "collect"):
        run_id_hint = (
            "Use a new run ID for each evaluation. "
            if name == "run"
            else "Use the existing run ID to resume collection. "
        )
        action = actions.add_parser(
            name,
            epilog=(
                run_id_hint + "Set UNITYHPC_CONTROLLER_URL and "
                "UNITYHPC_RUNTIME_ROOT when using another Unity account."
            ),
        )
        action.add_argument("job", type=Path)
        action.add_argument("--run-id", required=True)
        action.add_argument("--runner-config", type=Path, required=True)
        action.add_argument("--output", type=Path)
        action.add_argument("--poll-seconds", type=int, default=30)
        if name == "run":
            action.add_argument("--walltime-seconds", type=int)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise ValueError("--poll-seconds must be positive")
    root = Path(__file__).resolve().parents[1]
    job = args.job.resolve()
    output = args.output or job.parent / "output" / args.run_id
    runner = load_runner(root, "unityhpc", args.runner_config)
    if args.action == "run":
        validate_experiment(job)
        request = request_for(root, job, args.run_id, args.runner_config, args.walltime_seconds)
        submit_with_retries(runner, request)
        print(f"submitted {args.run_id}", flush=True)
    campaign = wait_for_terminal(runner, args.run_id, args.poll_seconds)
    collect(runner, job, args.run_id, output, campaign)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(
            "Monitoring stopped. The Unity run continues; use collect with the same run ID.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except (OSError, ValueError, ControllerError, ExecutionNotFoundError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
