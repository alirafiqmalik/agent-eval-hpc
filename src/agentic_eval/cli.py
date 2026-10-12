"""Run a Harbor job locally, or submit the same file to Unity."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_eval.jobs import validate_experiment
from agentic_eval.launch import run_job, unpack_run
from agentic_eval.unity import request_for
from runners.discovery import load_runner


def _runner(root: Path, args):
    return load_runner(root, args.runner, args.runner_config)


def _add_runner_args(command: argparse.ArgumentParser, *, job: bool) -> None:
    if job:
        command.add_argument("job", type=Path)
    command.add_argument("--run-id", required=True)
    command.add_argument("--runner", default="unityhpc")
    command.add_argument(
        "--runner-config",
        type=Path,
        default=Path("src/runners/unityhpc/connections/config.yaml"),
    )


def parser() -> argparse.ArgumentParser:
    commands = argparse.ArgumentParser(description="Run Harbor jobs locally or on Unity HPC")
    actions = commands.add_subparsers(dest="command", required=True)
    validate = actions.add_parser("validate", help="check a Harbor job file and its tasks")
    validate.add_argument("job", type=Path)
    run = actions.add_parser("run", help="run a Harbor job in the local sandbox")
    run.add_argument("job", type=Path)
    run.add_argument("--run-id", required=True)
    run.add_argument("--base-url", default=None)
    for name in ("plan", "submit"):
        command = actions.add_parser(name)
        _add_runner_args(command, job=True)
        command.add_argument("--walltime-seconds", type=int)
    for name in ("status", "cancel", "fetch"):
        command = actions.add_parser(name)
        _add_runner_args(command, job=False)
        if name == "fetch":
            command.add_argument("--output", type=Path, required=True)
    return commands


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            validate_experiment(args.job)
            print(f"valid {args.job}")
            return 0
        if args.command == "run":
            logs = args.job.resolve().parent / "output" / args.run_id / "logs"
            return run_job(args.job, logs, args.base_url)
        root = Path.cwd()
        runner = _runner(root, args)
        if args.command == "status":
            print(runner.status(args.run_id))
            return 0
        if args.command == "cancel":
            print(runner.cancel(args.run_id))
            return 0
        if args.command == "fetch":
            print(runner.fetch(args.run_id, args.output))
            unpack_run(args.output)
            return 0
        request = request_for(
            root, args.job, args.run_id, args.runner_config, args.walltime_seconds
        )
        if args.command == "plan":
            print(runner.plan(request))
            return 0
        print(runner.submit(request))
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
