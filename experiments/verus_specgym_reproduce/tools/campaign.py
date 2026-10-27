"""Resume a local-model reproduction: smoke gates, Harbor shards, fetch, and plots."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

import yaml

EXPERIMENT = Path(__file__).resolve().parents[1]
ROOT = EXPERIMENT.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from unity_eval import (  # noqa: E402
    IncompleteEvaluationError,
    collect,
    submit_with_retries,
    wait_for_terminal,
)

from agentic_eval.unity import request_for  # noqa: E402
from runners.discovery import load_runner  # noqa: E402
from runners.interface import ExecutionNotFoundError, ExecutionRequest, request_digest  # noqa: E402


class SmokeGateError(RuntimeError):
    """A collected smoke needs review; restarting cannot change its saved evidence."""


def save_json(path: Path, value: dict) -> None:
    """Commit a checkpoint atomically, including across driver interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def finished_trials(output: Path, tasks: list[str]) -> dict:
    """Keep fully graded trials even when another trial or the allocation failed."""
    receipt = output / "results/evaluation-manifest.json"
    summary_path = output / "results/summary.json"
    if not receipt.is_file() or not summary_path.is_file():
        return {}
    manifest = json.loads(receipt.read_text())
    summary = json.loads(summary_path.read_text())
    if not manifest.get("hardware_verified") or summary.get("audit_schema") != 2:
        return {}
    completed = {}
    for problem in summary["problems"]:
        if problem["task"] not in tasks:
            raise ValueError("Unexpected task in checkpoint source")
        if problem.get("exception_type") or problem.get("reward") is None:
            continue
        trial = output / "logs" / problem["trial"]
        raw = json.loads((trial / "result.json").read_text())
        if not raw.get("finished_at") or raw.get("verifier_result") is None:
            continue
        if problem["task"] in completed:
            raise ValueError("Duplicate completed trial in checkpoint source")
        completed[problem["task"]] = {
            **problem,
            "trial": str(trial.resolve()),
            "checkpoint_source": str(output.resolve()),
            "source_sha256": manifest.get("source_sha256"),
        }
    return completed


def run_checkpointed_shard(args, runner, record: dict) -> dict:
    """Resume at completed Harbor trials; never reset a partly executed agent as a resume."""
    record = copy.deepcopy(record)
    options = copy.copy(args)
    if connection := record.get("connection"):
        options.connection = Path(connection)
        runner = load_runner(ROOT, "unityhpc", options.connection)
    base_id = record.setdefault("base_id", record["id"])
    base_output = Path(record.setdefault("base_output", record["output"]))
    options.current_id = base_id
    fingerprint = request_digest(request(options, record["model"], record["tasks"], smoke=False))
    path = base_output / "task-checkpoints.json"
    ledger = (
        json.loads(path.read_text())
        if path.is_file()
        else {"request_sha256": fingerprint, "completed": {}, "attempts": {}}
    )
    if ledger["request_sha256"] != fingerprint:
        raise RuntimeError("Checkpoint configuration changed; start a new cohort")
    for attempt in range(args.shard_retries + 1):
        remaining = [task for task in record["tasks"] if task not in ledger["completed"]]
        if not remaining:
            break
        key = str(attempt)
        entry = ledger["attempts"].get(key)
        if entry and entry.get("collected"):
            continue
        if entry is None:
            entry = {
                "id": base_id if attempt == 0 else f"{base_id}-r{attempt}",
                "tasks": remaining,
                "output": str(base_output / f"attempt-{attempt}"),
            }
            ledger["attempts"][key] = entry
            save_json(path, ledger)  # Persist the exact subset before submitting it.
        options.current_id = entry["id"]
        req = request(options, record["model"], entry["tasks"], smoke=False)
        output = Path(entry["output"])
        if args.action == "collect":
            runner.status(req.id)  # Collection alone must not submit missing jobs.
        try:
            run_and_collect(runner, req, EXPERIMENT / "job.yaml", output)
        except IncompleteEvaluationError as exc:
            entry["error"] = str(exc)
        finished = finished_trials(output, entry["tasks"])
        for task, problem in finished.items():
            ledger["completed"].setdefault(task, problem)  # Preserve the first valid zero too.
        entry["collected"] = True
        save_json(path, ledger)
        print(
            f"{base_id}: checkpointed {len(ledger['completed'])}/{len(record['tasks'])} tasks",
            flush=True,
        )
        if args.action == "collect":
            break
    record["infrastructure_attempts"] = [x for x in ledger["attempts"].values() if x.get("error")]
    if set(ledger["completed"]) != set(record["tasks"]):
        record["failure"] = "Incomplete task checkpoints; artifacts and retry history retained"
        return record
    # This is an aggregate with provenance, not a fabricated single-job receipt.
    summary = {
        "audit_schema": 2,
        "checkpoint_mode": "completed-trials",
        "complete": True,
        "errors": 0,
        "passed": sum(p["passed"] for p in ledger["completed"].values()),
        "total": len(record["tasks"]),
        "expected_total": len(record["tasks"]),
        "problems": [ledger["completed"][task] for task in record["tasks"]],
    }
    save_json(base_output / "results/summary.json", summary)
    record.update(id=base_id, output=str(base_output), checkpoint_mode="completed-trials")
    record.pop("failure", None)
    return record


def run_and_collect(runner, request, job: Path, output: Path) -> dict:
    receipt = output / "results/evaluation-manifest.json"
    try:
        campaign = runner.status(request.id)
    except ExecutionNotFoundError:
        plan = runner.plan(request)
        output.mkdir(parents=True, exist_ok=True)
        (output / "placement.json").write_text(json.dumps(plan, indent=2) + "\n")
        if not plan["ready"]:
            raise RuntimeError(
                f"No eligible placement; inspect {output / 'placement.json'}"
            ) from None
        submit_with_retries(runner, request)
    else:
        if not runner.attest(request, campaign)["matched"]:
            raise RuntimeError(f"{request.id}: source or run controls changed; use a new run ID")
    if receipt.is_file() and json.loads(receipt.read_text()).get("complete"):
        summary = json.loads((output / "results/summary.json").read_text())
        if summary.get("audit_schema") != 2:
            raise RuntimeError("Pre-audit results require revalidation; use a new run ID")
        if summary.get("errors"):
            raise IncompleteEvaluationError(f"{request.id} has infrastructure failures")
        return summary
    campaign = wait_for_terminal(runner, request.id, 30)
    collect(runner, job, request.id, output, campaign)
    summary = json.loads((output / "results/summary.json").read_text())
    if summary["errors"]:
        raise IncompleteEvaluationError(f"{request.id} has infrastructure failures")
    return summary


def scientific_digest(experiment: Path) -> str:
    """Exclude orchestration changes, but require identical tasks, agent and scoring."""
    digest = hashlib.sha256()
    for name in (
        "job.yaml",
        "reproduction.yaml",
        "agent",
        "sandbox",
        "tasks",
        "tools/verifier",
        "tools/trajectory",
        "tools/eval",
    ):
        root = experiment / name
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        for path in paths:
            if path.is_file() and path.suffix != ".sif" and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(experiment)).encode() + b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


def reuse_completed(state: dict, previous: Path, args, runner) -> None:
    old = json.loads((previous / "campaign.json").read_text())
    for key in ("models", "reproduction", "execution", "runtime", "model_cache"):
        if key == "runtime" and key not in old:
            continue  # Legacy campaigns are checked against their controller receipt below.
        if old.get(key) != state.get(key):
            raise ValueError(f"Cannot reuse results with different {key}")
    current_digest = scientific_digest(EXPERIMENT)
    checked = set()
    for shard in old["shards"]:
        result = Path(shard["output"]) / "results"
        receipt = result / "evaluation-manifest.json"
        if not receipt.is_file():
            continue
        manifest = json.loads(receipt.read_text())
        if not manifest.get("complete") or not manifest.get("hardware_verified"):
            continue
        original = Path(manifest["job"]).parent
        if original not in checked:
            if scientific_digest(original) != current_digest:
                raise ValueError("Scientific inputs changed; previous results cannot be reused")
            options = copy.copy(args)
            options.current_id = manifest["run_id"]
            expected = request(options, shard["model"], shard["tasks"], smoke=False)
            observed = runner.status(manifest["run_id"])
            if observed.get("request_hash") != manifest["request_sha256"]:
                raise ValueError("Previous controller receipt changed")
            environment = observed["spec"]["tasks"][0]["env"]
            for key, value in expected.environment.items():
                if json.loads(environment[key]) != json.loads(value):
                    raise ValueError(f"Cannot reuse results with changed {key}")
            checked.add(original)
        summary = json.loads((result / "summary.json").read_text())
        if summary.get("audit_schema") != 2:
            raise ValueError(
                "Cannot reuse pre-audit results; transport timeouts were misclassified"
            )
        if not summary.get("complete") or summary.get("errors"):
            continue
        if sorted(p["task"] for p in summary["problems"]) != sorted(shard["tasks"]):
            raise ValueError("Previous shard has an inconsistent task inventory")
        state["shards"].append(
            {
                **shard,
                "reused_from": str(previous.resolve()),
                "source_sha256": manifest["source_sha256"],
            }
        )


def run_shard(args, runner, record: dict) -> dict:
    """Retry infrastructure failures with fresh IDs, keeping every failed artifact."""
    if getattr(args, "task_checkpoints", False):
        return run_checkpointed_shard(args, runner, record)
    record = copy.deepcopy(record)
    if connection := record.get("connection"):
        args = copy.copy(args)
        args.connection = Path(connection)
        runner = load_runner(ROOT, "unityhpc", args.connection)
    base_id = record.setdefault("base_id", record["id"])
    base_output = Path(record.setdefault("base_output", record["output"]))
    for attempt in range(args.shard_retries + 1):
        run_id = base_id if attempt == 0 else f"{base_id}-r{attempt}"
        output = (
            base_output
            if attempt == 0
            else base_output.with_name(base_output.name + f"-retry-{attempt}")
        )
        if args.action == "collect":
            run_id, output = record["id"], Path(record["output"])
        options = copy.copy(args)
        options.current_id = run_id
        req = request(options, record["model"], record["tasks"], smoke=False)
        if args.action == "collect":
            runner.status(req.id)
        try:
            run_and_collect(runner, req, EXPERIMENT / "job.yaml", output)
        except IncompleteEvaluationError as exc:
            record.setdefault("infrastructure_attempts", []).append(
                {"id": run_id, "output": str(output), "error": str(exc)}
            )
            print(f"{run_id}: infrastructure attempt failed: {exc}", flush=True)
            if args.action == "collect" or attempt == args.shard_retries:
                record["failure"] = str(exc)
                return record
            continue
        record.update(id=run_id, output=str(output))
        record.pop("failure", None)
        return record
    raise AssertionError("unreachable")


def parallel_shards(args, runner, records: list[dict], state: dict, path: Path) -> None:
    """Bound submissions, save each result, and stop replenishing on repeated failures."""
    pending = iter(records)
    failures = []

    def save(record):
        index = next(
            i
            for i, old in enumerate(state["shards"])
            if old.get("base_id", old["id"]) == record.get("base_id", record["id"])
        )
        state["shards"][index] = record
        save_json(path, state)

    with ThreadPoolExecutor(max_workers=args.parallel_shards) as pool:
        active = {}

        def replenish():
            while len(active) < args.parallel_shards and len(failures) < args.parallel_shards:
                record = next(pending, None)
                if record is None:
                    return
                fast = getattr(args, "fast_connection", None)
                if fast and "connection" not in record:
                    fast_busy = any(r.get("connection") == str(fast) for r in active.values())
                    record = {**record, "connection": str(args.connection if fast_busy else fast)}
                    save(record)
                active[pool.submit(run_shard, args, runner, record)] = record

        replenish()
        while active:
            finished, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in finished:
                record = active.pop(future)
                try:
                    record = future.result()
                except Exception as exc:
                    record = {**record, "failure": str(exc)}
                save(record)
                if record.get("failure"):
                    failures.append(record["id"])
            replenish()
    if failures:
        raise RuntimeError(f"Unresolved shards (artifacts retained; rerun to resume): {failures}")


def prepare_models(args, runner, models: list[str], output: Path) -> None:
    """Weights are staged on CPUs and checked before requesting scarce GPUs."""
    runtime = yaml.safe_load((EXPERIMENT / "runner.yaml").read_text())["preparation"]
    run_id = args.download_run_id or f"{args.run_id}-download"
    argv = [
        str(Path(runner.config["runtime_root"]) / ".venv/bin/python"),
        "experiments/verus_specgym_reproduce/tools/download_models.py",
        "--cache",
        str(args.model_cache),
    ]
    for model in models:
        argv.extend(["--model", model])
    req = ExecutionRequest(
        run_id,
        ROOT,
        tuple(argv),
        ("model-downloads.json",),
        runtime["hardware"],
        10800 if args.suite == "early" else runtime["walltime_seconds"],
    )
    try:
        runner.status(run_id)
    except ExecutionNotFoundError:
        if args.download_run_id:
            raise
        submit_with_retries(runner, req)
    status = wait_for_terminal(runner, run_id, 30)
    runner.fetch(run_id, output / "preparation")
    receipt = output / "preparation/evaluation/model-downloads.json"
    if status["state"] != "completed" or not receipt.is_file():
        raise RuntimeError(f"Model preparation failed: {run_id}")
    downloaded = json.loads(receipt.read_text())
    settings = yaml.safe_load((EXPERIMENT / "reproduction.yaml").read_text())["models"]
    for model in models:
        expected = {
            "repository": settings[model]["repository"],
            "revision": settings[model]["revision"],
            "path": str(args.model_cache / model),
        }
        if downloaded.get(model) != expected:
            raise RuntimeError(f"Model preparation receipt differs for {model}")


def validate_repeated_checker_control(summary: dict, output: Path) -> None:
    repeated = [p for p in summary["problems"] if p.get("agent_public_checks") == 2]
    if len(repeated) != 1 or not repeated[0]["passed"]:
        raise RuntimeError("Repeated public-checker control did not complete")
    logs = output / "logs" / repeated[0]["trial"] / "agent"
    original = (logs / "first-check.sha256").read_text().split()[0]
    first = logs / "compressed_verdicts/1/results.zip"
    second = logs / "compressed_verdicts/2/results.zip"
    if not second.is_file() or hashlib.sha256(first.read_bytes()).hexdigest() != original:
        raise RuntimeError("Public-checker retry overwrote earlier evidence")


def verifier_controls(args, runner, output: Path) -> None:
    """Exercise positive, syntax-failure and all-true controls before evaluation."""
    runtime = yaml.safe_load((EXPERIMENT / "runner.yaml").read_text())["preparation"]
    job = EXPERIMENT / "oracle-smoke.yaml"
    req = dataclasses.replace(
        request_for(ROOT, job, f"{args.run_id}-verifier", args.connection),
        hardware=runtime["hardware"],
        walltime_seconds=10800,
        environment={"EVAL_MODEL_SERVER_MODE": "none", "EVAL_AGENT_CONFIG": '{"model":"oracle"}'},
    )
    summary = run_and_collect(runner, req, job, output / "smoke/verifier")
    if summary["total"] != 4 or summary["passed"] != 2:
        raise RuntimeError("Verifier control count or reward is incorrect")
    positive = [p for p in summary["problems"] if p["task"] == "verus-gym-50-a"]
    if len(positive) != 2 or not all(p["passed"] for p in positive):
        raise RuntimeError("Known-correct specification did not pass")
    validate_repeated_checker_control(summary, output / "smoke/verifier")
    negatives = [p for p in summary["problems"] if p["task"] == "verus-gym-1009-a"]
    syntax = sorted(p["syntax_passed"] for p in negatives)
    if syntax != [False, True]:
        raise RuntimeError("Verifier did not distinguish invalid and valid syntax")
    valid = next(p for p in negatives if p["syntax_passed"])
    for bucket, counts in valid["buckets"].items():
        expected = counts["total"] if bucket.endswith("complete") else 0
        if counts["passed"] != expected:
            raise RuntimeError(f"All-true specification has unexpected {bucket} results")


def prepare_server_runtime(args, runner, runtime: dict, output: Path) -> None:
    if not runtime["model_server"].get("remote_worker"):
        return
    req = ExecutionRequest(
        args.run_id + "-arm-setup",
        ROOT,
        (
            "bash",
            "experiments/verus_specgym_reproduce/tools/setup-arm.sh",
            runner.config["server_runtime_root"],
        ),
        ("arm-runtime.json",),
        runtime["hardware"],
        3600,
    )
    try:
        runner.status(req.id)
    except ExecutionNotFoundError:
        submit_with_retries(runner, req)
    state = wait_for_terminal(runner, req.id, 30)
    runner.fetch(req.id, output / "arm-preparation")
    if state["state"] != "completed":
        raise RuntimeError("ARM inference runtime preparation failed")


def execution_settings(args, reproduction: dict) -> dict:
    settings = dict(reproduction["local_execution"])
    if args.suite == "early":
        settings.update(reproduction["early_execution"])
    settings.update(yaml.safe_load(args.runtime_config.read_text()).get("evaluation", {}))
    if timeout := getattr(args, "inference_timeout_seconds", None):
        # This bounds HTTP waiting, not agent execution. Harbor still enforces
        # the original task deadline and SWE-agent retains its call/token limits.
        settings["completion_kwargs"] = {
            **settings.get("completion_kwargs", {}),
            "timeout": timeout,
        }
    return settings


def validate_model_smoke(summary: dict, expected: int) -> None:
    if not summary.get("complete") or summary["total"] != expected:
        raise SmokeGateError("Smoke did not complete exactly the requested tasks")
    failures = {}
    for problem in summary["problems"]:
        reasons = []
        if not problem.get("agent_source_changed"):
            reasons.append("source_not_changed")
        if not problem.get("agent_public_checks"):
            reasons.append("no_public_checker_result")
        if "agent_checker_error_steps" not in problem:
            reasons.append("missing_checker_health_evidence")
        elif problem.get("agent_checker_error_steps"):
            reasons.append("checker_exception")
        if problem.get("termination"):
            reasons.append("agent_termination")
        if any(
            kind in (problem.get("agent_exit_status") or "")
            for kind in ("exit_format", "exit_context")
        ):
            reasons.append("format_or_context_exit")
        if problem.get("agent_truncated_calls"):
            reasons.append("truncated_model_reply")
        if reasons:
            failures[problem.get("task", "unknown")] = reasons
    if failures:
        raise SmokeGateError(
            "Smoke did not finish an untruncated edit-and-check interaction "
            "with a healthy checker: " + json.dumps(failures, sort_keys=True)
        )


def request(args, model_name: str, tasks: list[str], *, smoke: bool):
    reproduction = yaml.safe_load((EXPERIMENT / "reproduction.yaml").read_text())
    model = reproduction["models"][model_name]
    settings = execution_settings(args, reproduction)
    job = yaml.safe_load((EXPERIMENT / "job.yaml").read_text())
    job["datasets"][0]["task_names"] = tasks
    job["job_name"] = args.current_id
    job["n_concurrent_trials"] = settings["concurrent_trials"]
    job["agents"][0]["kwargs"]["max_input_tokens"] = str(settings["max_input_tokens"])
    job["agents"][0]["kwargs"]["completion_kwargs"] = {
        "max_tokens": settings["max_output_tokens"],
        **settings.get("completion_kwargs", {}),
    }
    for key in ("temperature", "top_p"):
        job["agents"][0]["kwargs"][key] = str(settings[key])
    if smoke:
        job["n_concurrent_trials"] = min(len(tasks), settings["concurrent_trials"])
        job["agents"][0]["override_timeout_sec"] = settings["smoke_base_timeout_seconds"]
        job["agents"][0]["env"]["SWEAGENT_CONFIG"] = "/opt/specgym-smoke-swe.yaml"
    agent = yaml.safe_load((EXPERIMENT / "agent/agent-config.yaml").read_text())
    agent["model"] = str(args.model_cache / model_name)
    runtime = yaml.safe_load(args.runtime_config.read_text())
    server = runtime["model_server"]
    server["context_length"] = settings["context_length"]
    server["command"] = shlex.join(
        [
            "env",
            *[f"{key}={value}" for key, value in model.get("server_environment", {}).items()],
            "vllm",
            "serve",
            "{model}",
            "--served-model-name",
            "specgym",
            "--port",
            "{port}",
            "--tensor-parallel-size",
            str(model["tensor_parallel_size"]),
            "--max-model-len",
            "{context_length}",
            "--max-num-seqs",
            str(settings["concurrent_trials"]),
            "--tool-call-parser",
            model["tool_parser"],
            "--reasoning-parser",
            model["reasoning_parser"],
            "--enable-auto-tool-choice",
            "--trust-remote-code",
            "--chat-template-content-format",
            "string",
        ]
    )
    extra = [
        value.replace("{", "{{").replace("}", "}}")
        for value in [*model.get("vllm_args", []), *server.get("extra_args", [])]
    ]
    if extra:
        server["command"] += " " + shlex.join(extra)
    req = request_for(ROOT, EXPERIMENT / "job.yaml", args.current_id, args.connection)
    return dataclasses.replace(
        req,
        hardware=runtime["hardware"],
        # Checkpoint retries belong to the task ledger, not an opaque whole-job retry.
        max_attempts=1
        if getattr(args, "task_checkpoints", False) and not smoke
        else req.max_attempts,
        walltime_seconds=runtime.get("smoke_walltime_seconds", 10800)
        if smoke
        else runtime["walltime_seconds"],
        environment={
            "EVAL_AGENT_CONFIG": json.dumps(agent),
            "EVAL_RUNNER_CONFIG": json.dumps(runtime),
            "EVAL_JOB_CONFIG": json.dumps(job),
        },
    )


def main() -> None:
    global ROOT, EXPERIMENT
    reproduction = yaml.safe_load((EXPERIMENT / "reproduction.yaml").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "smoke", "run", "collect"], nargs="?", default="run"
    )
    parser.add_argument(
        "--run-id", default="specgym-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    )
    parser.add_argument("--suite", choices=["paper", "early"], default="paper")
    parser.add_argument("--model", action="append", choices=list(reproduction["models"]))
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--model-cache", type=Path, default=os.environ.get("SPECGYM_MODEL_CACHE"))
    parser.add_argument("--download-run-id", help="Reuse an existing CPU model download campaign")
    parser.add_argument("--parallel-shards", type=int, default=1)
    parser.add_argument(
        "--fast-connection",
        type=Path,
        help="Use one shard lane on a limited fast QoS alongside normal lanes",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Run updated orchestration against an immutable scientific bundle",
    )
    parser.add_argument("--shard-retries", type=int, default=2)
    parser.add_argument("--task-checkpoints", action="store_true")
    parser.add_argument("--inference-timeout-seconds", type=int)
    parser.add_argument(
        "--controls-from", type=Path, help="Reuse an identical verified control run"
    )
    parser.add_argument(
        "--reuse-completed",
        type=Path,
        help="Reuse verified shards with identical scientific inputs and hardware",
    )
    parser.add_argument(
        "--connection",
        type=Path,
    )
    args = parser.parse_args()
    if args.inference_timeout_seconds is not None and args.inference_timeout_seconds <= 0:
        parser.error("Inference request timeout must be positive")
    if args.source_root:
        ROOT = args.source_root.resolve()
        EXPERIMENT = ROOT / "experiments/verus_specgym_reproduce"
        reproduction = yaml.safe_load((EXPERIMENT / "reproduction.yaml").read_text())
    if args.parallel_shards < 1 or args.shard_retries < 0:
        parser.error("Parallel shards must be positive; retries must be nonnegative")
    if args.connection is None:
        args.connection = (
            ROOT
            / "src/runners/unityhpc/connections"
            / ("verus-specgym.yaml" if args.suite == "paper" else "short.yaml")
        )
    if args.runtime_config is None:
        args.runtime_config = EXPERIMENT / (
            "runner.yaml" if args.suite == "paper" else "runner-early.yaml"
        )
    connection = yaml.safe_load(args.connection.read_text())
    if args.model_cache is None and connection.get("model_cache"):
        args.model_cache = Path(connection["model_cache"])
    if args.model_cache is None:
        args.model_cache = Path(
            yaml.safe_load(
                (ROOT / "src/runners/unityhpc/connections/verus-specgym.yaml").read_text()
            )["model_cache"]
        )
    if args.model_cache is None or not args.model_cache.is_absolute():
        parser.error(
            "Set --model-cache to the Unity scratch model directory (or SPECGYM_MODEL_CACHE)"
        )
    manifest = json.loads((EXPERIMENT / "tasks/manifest.json").read_text())
    if args.action in ("run", "collect") and (manifest["missing"] or manifest["imported"] != 581):
        raise RuntimeError("All 581 task archives are required for a full reproduction")
    models = args.model or [
        name for name, model in reproduction["models"].items() if model["suite"] == args.suite
    ]
    if any(reproduction["models"][model]["suite"] != args.suite for model in models):
        parser.error("Selected models do not belong to --suite")
    execution = execution_settings(args, reproduction)
    runtime = yaml.safe_load(args.runtime_config.read_text())
    output = EXPERIMENT / "output" / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "campaign.json"
    runner = load_runner(ROOT, "unityhpc", args.connection)
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        if state["models"] != models or state["reproduction"] != reproduction:
            raise RuntimeError("Existing campaign configuration differs; use a new run ID")
        if state.get("execution") != execution:
            raise RuntimeError("Existing campaign execution settings differ; use a new run ID")
        if state.get("runtime", runtime) != runtime:
            raise RuntimeError(
                "Existing campaign hardware/server settings differ; use a new run ID"
            )
        if state.get("task_checkpoints", False) != args.task_checkpoints:
            raise RuntimeError("Checkpoint policy changed; use a new run ID")
        if state.get("source_root", str(ROOT)) != str(ROOT):
            raise RuntimeError("Frozen source changed; use a new run ID")
    else:
        if args.action == "collect":
            raise RuntimeError("No saved campaign to collect")
        state = {
            "run_id": args.run_id,
            "models": models,
            "reproduction": reproduction,
            "shards": [],
            "model_cache": str(args.model_cache),
            "suite": args.suite,
            "execution": execution,
            "runtime": runtime,
            "configuration_label": runtime.get("configuration_label"),
            "task_checkpoints": args.task_checkpoints,
            "source_root": str(ROOT),
        }
        if args.reuse_completed:
            reuse_completed(state, args.reuse_completed, args, runner)
    if state["model_cache"] != str(args.model_cache):
        raise RuntimeError("Existing campaign uses a different checkpoint directory")
    save_json(state_path, state)
    lease_connection = ROOT / "src/runners/unityhpc/connections/verus-specgym.yaml"
    lease_config = yaml.safe_load(lease_connection.read_text())
    if args.action != "collect" and str(args.model_cache) == lease_config.get("model_cache"):
        with (output / "workspace-lease.log").open("a") as stream:
            subprocess.Popen(
                [
                    sys.executable,
                    str(EXPERIMENT / "tools/workspace_lease.py"),
                    "--connection",
                    str(lease_connection),
                    "--parent-pid",
                    str(os.getpid()),
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
    if connection.get("gpu_request_by_constraint"):
        health = runner.client.json("GET", "/api/v1/health")
        if "gpu_request_by_constraint" not in health.get("capabilities", []):
            raise RuntimeError("Unity controller 0.16.2+ is required for H200 constraint requests")
    if args.action != "collect":
        for name, route in (("backend", "/api/v1/backend"), ("queue", "/api/v1/cluster/queue")):
            snapshot = runner.client.json("GET", route)
            (output / f"recon-{name}.json").write_text(json.dumps(snapshot, indent=2) + "\n")
        prepare_models(args, runner, models, output)
        if args.action == "prepare":
            print(f"Model preparation complete: {output / 'preparation'}")
            return
        prepare_server_runtime(args, runner, runtime, output)
        if args.controls_from:
            from audit import control_signature

            controls = args.controls_from.resolve()
            receipt = json.loads((controls / "results/evaluation-manifest.json").read_text())
            original = Path(receipt["job"]).parents[2]
            if not receipt.get("complete") or not receipt.get("hardware_verified"):
                raise RuntimeError("Shared controls are incomplete")
            if control_signature(original) != control_signature(ROOT):
                raise RuntimeError("Shared controls differ from this frozen source")
            summary = json.loads((controls / "results/summary.json").read_text())
            if summary["total"] != 5 or summary["passed"] != 3 or summary["errors"]:
                raise RuntimeError("Shared verifier controls failed")
            validate_repeated_checker_control(summary, controls)
            save_json(output / "shared-controls.json", {"path": str(controls)})
        else:
            verifier_controls(args, runner, output)
    task_names = [item["task"] for item in manifest["tasks"]]
    shard_size = execution["tasks_per_shard"]
    # Every selected model must pass an end-to-end smoke before any full run.
    for model in models:
        args.current_id = f"{args.run_id}-{model}-smoke"
        smoke_tasks = reproduction["local_execution"]["smoke_tasks"]
        smoke_args = copy.copy(args)
        smoke_runner = runner
        if args.fast_connection:
            smoke_args.connection = args.fast_connection
            smoke_runner = load_runner(ROOT, "unityhpc", args.fast_connection)
        req = request(smoke_args, model, smoke_tasks, smoke=True)
        smoke_dir = output / "smoke" / model
        if args.action == "collect":
            receipt = smoke_dir / "results/evaluation-manifest.json"
            if not receipt.is_file() or not json.loads(receipt.read_text())["complete"]:
                raise RuntimeError(f"No completed smoke for {model}")
        else:
            summary = run_and_collect(smoke_runner, req, EXPERIMENT / "job.yaml", smoke_dir)
            validate_model_smoke(summary, len(smoke_tasks))
    if args.action == "smoke":
        print(f"All {len(models)} model smoke tests passed: {output}")
        return
    work = []
    for model in models:
        reused = {
            task
            for shard in state["shards"]
            if shard.get("reused_from") and shard["model"] == model
            for task in shard["tasks"]
        }
        remaining = [task for task in task_names if task not in reused]
        for offset in range(0, len(remaining), shard_size):
            number = offset // shard_size
            args.current_id = f"{args.run_id}-{model}-{number:03d}"
            tasks = remaining[offset : offset + shard_size]
            shard_dir = output / "shards" / model / f"{number:03d}"
            record = {
                "id": args.current_id,
                "model": model,
                "tasks": tasks,
                "output": str(shard_dir),
            }
            existing = next(
                (s for s in state["shards"] if s.get("base_id", s["id"]) == args.current_id), None
            )
            if existing is None:
                if args.action == "collect":
                    continue
                state["shards"].append(copy.deepcopy(record))
            else:
                record = existing
            work.append(record)
    save_json(state_path, state)
    parallel_shards(args, runner, work, state, state_path)
    subprocess.run(
        [sys.executable, str(EXPERIMENT / "tools/eval/plots.py"), str(output)], check=True
    )
    print(f"Collected reproduction results: {output / 'results'}")


if __name__ == "__main__":
    try:
        main()
    except SmokeGateError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(78) from exc
