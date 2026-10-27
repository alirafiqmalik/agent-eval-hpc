"""Run verifier controls and a bounded model health check; never launch full shards."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml
from batch import RELATIVE, freeze
from campaign import (
    IncompleteEvaluationError,
    request,
    run_and_collect,
    validate_model_smoke,
    validate_repeated_checker_control,
)

from agentic_eval.unity import request_for
from runners.discovery import load_runner


def run_model_diagnostic(source: Path, options, tasks: list[str], *, smoke: bool, output: Path):
    """Fall back once after a confirmed short-QoS rejection before any job exists."""
    runner = load_runner(source, "unityhpc", options.connection)
    req = request(options, "qwen3.5-9b", tasks, smoke=smoke)
    job = source / RELATIVE / "job.yaml"
    try:
        return run_and_collect(runner, req, job, output)
    except IncompleteEvaluationError:
        failed = runner.status(req.id)
        output.mkdir(parents=True, exist_ok=True)
        (output / "controller-failure.json").write_text(json.dumps(failed, indent=2) + "\n")
        rejected = failed.get("tasks", [])
        if not (
            runner.config.get("qos") == "short"
            and failed.get("state") == "failed"
            and rejected
            and all(
                task.get("state") == "rejected"
                and task.get("job_id") is None
                and "QOSMaxSubmitJobPerUserLimit" in (task.get("error") or "")
                for task in rejected
            )
            and not any(attempt.get("job_id") for attempt in failed.get("attempts", []))
        ):
            raise
    # Preserve the rejected identity and receipt. Rebuild the same scientific
    # request with a distinct job name and the eligible normal-QoS connection.
    fallback = argparse.Namespace(**vars(options))
    fallback.connection = source / "src/runners/unityhpc/connections/specgym-a100-parallel.yaml"
    fallback.current_id += "-qn"
    runner = load_runner(source, "unityhpc", fallback.connection)
    req = request(fallback, "qwen3.5-9b", tasks, smoke=smoke)
    destination = output / "normal-qos"
    (output / "queue-fallback.json").write_text(
        json.dumps(
            {
                "rejected_run": options.current_id,
                "reason": "QOSMaxSubmitJobPerUserLimit",
                "fallback_run": req.id,
                "connection": str(fallback.connection),
                "output": str(destination),
                "scientific_settings_changed": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Short QoS submit limit reached; retrying diagnostic as {req.id}", flush=True)
    return run_and_collect(runner, req, job, destination)


def control_signature(source: Path) -> str:
    """Permit control reuse only when the container, grading and controls match."""
    digest = hashlib.sha256()
    for relative in (
        RELATIVE / "sandbox",
        RELATIVE / "agent",
        RELATIVE / "tools/verifier",
        RELATIVE / "tools/eval/upstream_scores.py",
        RELATIVE / "tasks/diagnostics",
        RELATIVE / "oracle-audit.yaml",
        Path("src/runners/unityhpc/environment.py"),
        Path("src/agentic_eval/launch.py"),
        Path("src/agentic_eval/preinstalled_swe_agent.py"),
    ):
        location = source / relative
        for path in sorted(location.rglob("*")) if location.is_dir() else [location]:
            if path.is_file() and path.suffix != ".sif" and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(source)).encode() + b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controls-from", type=Path, help="Reuse verified, identical CPU controls")
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument(
        "--full-budget", action="store_true", help="Four diagnostic tasks at the full agent budget"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = freeze(output)
    experiment = source / RELATIVE
    # The controller selects placement from these resource requests; no direct
    # Slurm submission or GPU allocation occurs outside the controller.
    connection = source / "src/runners/unityhpc/connections/specgym-a100-parallel.yaml"
    runner = load_runner(source, "unityhpc", connection)
    job = experiment / "oracle-audit.yaml"
    prep = yaml.safe_load((experiment / "runner.yaml").read_text())["preparation"]
    req = dataclasses.replace(
        request_for(source, job, args.run_id + "-cpu-controls-t610-v2", connection),
        hardware=prep["hardware"],
        walltime_seconds=3600,
        environment={"EVAL_MODEL_SERVER_MODE": "none", "EVAL_AGENT_CONFIG": '{"model":"oracle"}'},
    )
    controls = output / "controls"
    if args.controls_from:
        controls = args.controls_from.resolve()
        manifest = json.loads((controls / "results/evaluation-manifest.json").read_text())
        original = Path(manifest["job"]).parents[2]
        if (
            not manifest.get("complete")
            or not manifest.get("hardware_verified")
            or control_signature(original) != control_signature(source)
        ):
            raise RuntimeError("Cannot reuse incomplete or changed verifier controls")
        summary = json.loads((controls / "results/summary.json").read_text())
        (output / "reused-controls.json").write_text(
            json.dumps(
                {"controls": str(controls), "signature": control_signature(source)}, indent=2
            )
            + "\n"
        )
    else:
        summary = run_and_collect(runner, req, job, controls)
    if summary["total"] != 5 or summary["passed"] != 3 or summary["errors"]:
        raise RuntimeError("Positive/negative verifier controls failed")
    positive = [p for p in summary["problems"] if p["task"] == "verus-gym-50-a"]
    if len(positive) != 3 or not all(p["passed"] for p in positive):
        raise RuntimeError("All positive controls must pass all hidden buckets")
    validate_repeated_checker_control(summary, controls)
    durations = []
    for problem in positive:
        result = json.loads((controls / "logs" / problem["trial"] / "result.json").read_text())
        phase = result["agent_execution"]
        durations.append(
            (
                datetime.fromisoformat(phase["finished_at"])
                - datetime.fromisoformat(phase["started_at"])
            ).total_seconds()
        )
    if max(durations) < 610:
        raise RuntimeError("The delayed positive control did not cross the old HTTP timeout")
    print("Positive/negative controls and the >600-second transport probe passed", flush=True)
    if args.controls_only:
        (output / "controls-passed.json").write_text(
            json.dumps({"controls": str(controls), "signature": control_signature(source)}) + "\n"
        )
        return
    # Use the frozen scientific inputs for the model request as well.
    import campaign

    campaign.ROOT, campaign.EXPERIMENT = source, experiment
    connection = source / "src/runners/unityhpc/connections/short.yaml"
    options = argparse.Namespace(
        suite="early",
        runtime_config=experiment / "runner-audit-a100.yaml",
        connection=connection,
        model_cache=Path(
            yaml.safe_load(
                (source / "src/runners/unityhpc/connections/verus-specgym.yaml").read_text()
            )["model_cache"]
        ),
        current_id=args.run_id
        + (
            "-q9-a100-c128k-o32k-t06-p95-c400-4t-diagnostic"
            if args.full_budget
            else "-q9-a100-c128k-o32k-t06-p95-2t-smoke"
        ),
    )
    tasks = yaml.safe_load((experiment / "reproduction.yaml").read_text())["local_execution"][
        "smoke_tasks"
    ]
    if args.full_budget:
        tasks = yaml.safe_load(options.runtime_config.read_text())["diagnostic_tasks"]
    destination = output / ("full-budget" if args.full_budget else "model-smoke")
    summary = run_model_diagnostic(
        source, options, tasks, smoke=not args.full_budget, output=destination
    )
    if args.full_budget:
        # A small diagnostic is not a dataset reproduction or permission to
        # scale. Preserve failures for review, regardless of the pass count.
        report = {
            "controls_passed": True,
            "agent_budget": {"calls": 400, "seconds": 3600},
            "tasks": tasks,
            "passed": summary["passed"],
            "errors": summary["errors"],
            "problems": summary["problems"],
            "full_runs_started": False,
            "scale_up_ready": False,
            "review_required": True,
        }
        (output / "full-budget-review.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Full-budget diagnostic collected; full runs remain held. Results: {output}")
        return
    validate_model_smoke(summary, len(tasks))
    (output / "audit-passed.json").write_text(
        json.dumps(
            {
                "controls": 5,
                "model_tasks": tasks,
                "full_runs_started": False,
                "scale_up_ready": False,
                "full_budget_validated": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Audit passed; full runs remain held. Results: {output}", flush=True)


if __name__ == "__main__":
    main()
