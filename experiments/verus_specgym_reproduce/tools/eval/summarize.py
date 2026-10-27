"""Summarize all Harbor attempts, retaining failures and infrastructure errors."""

from __future__ import annotations

import json
import re
import sys
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

from upstream_scores import BUCKETS, load_task_score


def agent_exception(exception: dict) -> str | None:
    kind = exception.get("exception_type") or exception.get("type")
    if kind != "AgentTimeoutError":
        return kind
    # Harbor wraps transport timeouts in AgentTimeoutError even when the task
    # still has time remaining. These are broken runs, not benchmark zeros.
    if "HTTP request timed out" in json.dumps(exception):
        return "ContainerTransportTimeout"
    return None


def agent_health(trial: Path, template: Path) -> dict:
    finishes = Counter()
    for debug in (trial / "agent/swe-agent-output").rglob("*.debug.log"):
        with debug.open() as stream:
            for line in stream:
                if " - swea-lm - Response: " in line:
                    match = re.search(r"Choices\(finish_reason='([^']+)'", line)
                    if match:
                        finishes[match[1]] += 1
    submitted = trial / "verifier/solve.rs"
    changed = None
    if template.is_file() and submitted.is_file():
        with tarfile.open(template) as bundle:
            changed = submitted.read_bytes() != bundle.extractfile("task/solve_template.rs").read()
    # These archives are emitted by successful public-checker invocations,
    # separately from the hidden verifier's archive.
    public_checks = list((trial / "agent/compressed_verdicts").glob("*/results.zip"))
    checker_errors = []
    trajectory = trial / "agent/swe-agent.trajectory.json"
    if trajectory.is_file():
        for index, step in enumerate(json.loads(trajectory.read_text()).get("trajectory", [])):
            if "verus_gym_specgen_check" in (
                step.get("action") or ""
            ) and "Traceback (most recent call last)" in (step.get("observation") or ""):
                checker_errors.append(index)
    return {
        "agent_finish_reasons": dict(finishes),
        "agent_truncated_calls": finishes["length"],
        "agent_source_changed": changed,
        "agent_public_checks": len(public_checks),
        # Retain these as agent/tool-health evidence, without converting an
        # agent-induced checker failure into a different benchmark reward.
        "agent_checker_error_steps": checker_errors,
    }


def resolution(item: dict) -> str:
    for kind in ("soundness", "completeness"):
        runtime = item.get(f"execution_{kind}_verdict") or {}
        if (runtime.get("compile_verdict") or {}).get("exit_code", 0) != 0:
            return "compile_error"
    symbolic = item.get("symbolic_verdict")
    if symbolic in ("sound", "complete"):
        return "symbolic_correct" if symbolic == item["expected_verdict"] else "symbolic_incorrect"
    execution = item.get("execution_verdict")
    if execution in ("sound", "complete"):
        return "exec_correct" if execution == item["expected_verdict"] else "exec_incorrect"
    return "indeterminate"


def summarize(run_dir: Path) -> dict:
    experiment = Path(__file__).resolve().parents[2]
    manifest = json.loads((experiment / "tasks/manifest.json").read_text())
    totals = {item["task"]: item["bucket_totals"] for item in manifest["tasks"]}
    jobs = []
    records = []
    for path in sorted((run_dir / "logs").rglob("result.json")):
        data = json.loads(path.read_text())
        if "task_name" not in data:
            if "n_total_trials" in data:
                jobs.append(data)
            continue
        name = data["task_name"].split("/")[-1]
        score = load_task_score(name, path.parent)
        exception = data.get("exception_info") or {}
        exception_type = exception.get("exception_type") or exception.get("type")
        record = {
            "task": name,
            "trial": str(path.parent.relative_to(run_dir / "logs")),
            "model": ((data.get("agent_info") or {}).get("model_info") or {}).get("name"),
            "reward": score.reward if score else None,
            "passed": bool(score and score.did_pass),
            # The task's wall-time budget is an experimental stopping condition.
            "exception_type": agent_exception(exception),
            "termination": exception_type,
            "buckets": {},
            "resolutions": {},
        }
        record.update(
            agent_health(
                path.parent,
                experiment / "tasks/verus-specgym" / name / "environment/files/context.tar.gz",
            )
        )
        archive = path.parent / "verifier/compressed_verdicts/run_on_all_samples/results.zip"
        feedback = None
        if archive.is_file():
            with zipfile.ZipFile(archive) as bundle:
                feedback = json.loads(bundle.read("EvaluationFeedback.json"))
        record["syntax_passed"] = (
            (feedback.get("syntax_check_feedback") or {}).get("has_passed_syntax_checks")
            if feedback
            else None
        )
        for bucket in BUCKETS:
            count = totals[name][bucket]
            value = score.buckets[bucket] if score else None
            # The upstream parser uses 0/0 for syntax failures. Retain the actual
            # hidden-test denominator so failures are included in the macro average.
            passed = value.num_passed if value else 0
            observed = value.num_total if value else 0
            record["buckets"][bucket] = {"passed": passed, "total": count}
            if record["syntax_passed"] and observed != count:
                record["exception_type"] = "IncompleteEvaluatorFeedback"
            if feedback and record["syntax_passed"]:
                cases = (feedback.get("execution_feedback") or {}).get(bucket) or {}
                record["resolutions"][bucket] = dict(Counter(resolution(x) for x in cases.values()))
            elif feedback:
                record["resolutions"][bucket] = {"compile_error": count}
        if feedback is None and not record["exception_type"]:
            record["exception_type"] = "MissingEvaluatorFeedback"
        compiler_check = path.parent / "verifier/toolchain-check.json"
        if (
            not compiler_check.is_file()
            or json.loads(compiler_check.read_text()).get("return_code") != 0
        ):
            record["exception_type"] = record["exception_type"] or "MissingOrFailedToolchainCheck"
        if score and score.reward is not None and score.reward != float(score.did_pass):
            record["exception_type"] = "RewardFeedbackMismatch"
        trajectory = path.parent / "agent/swe-agent.trajectory.json"
        if trajectory.is_file():
            trace = json.loads(trajectory.read_text())
            record["agent_exit_status"] = (trace.get("info") or {}).get("exit_status")
            exit_kind = (
                (record["agent_exit_status"] or "").removeprefix("submitted (").removesuffix(")")
            )
            record["agent_steps"] = len(trace.get("trajectory") or [])
            record["agent_actions"] = sum(
                bool(step.get("action")) for step in trace.get("trajectory") or []
            )
            if exit_kind in ("exit_api", "exit_environment_error", "exit_error"):
                record["exception_type"] = "SweAgentInfrastructureError"
        elif (data.get("agent_info") or {}).get("name") == "swe-agent" and not exception_type:
            record["exception_type"] = "MissingAgentTrajectory"
        records.append(record)
    expected = sum(job["n_total_trials"] for job in jobs) if jobs else None
    errors = sum(bool(r["exception_type"]) for r in records)
    summary = {
        "audit_schema": 2,
        "passed": sum(r["passed"] for r in records),
        "total": len(records),
        "expected_total": expected,
        "errors": errors,
        "complete": bool(jobs)
        and expected == len(records)
        and errors == 0
        and all(j.get("finished_at") for j in jobs)
        and all(r["reward"] is not None for r in records),
        "problems": records,
    }
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    summarize(Path(sys.argv[1]))
