"""Evaluation integrity: missing verifier output must not look like a valid zero."""

import argparse
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments/verus_specgym_reproduce"
TASK = "verus-gym-1009-a"
BUCKETS = ("pre_sound", "pre_complete", "post_complete", "post_sound")


def scored_run(tmp_path, feedback, reward=0, exception=None):
    trial = tmp_path / "logs/job" / TASK
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(exist_ok=True)
    (trial / "verifier/toolchain-check.json").write_text('{"return_code": 0}')
    (trial.parent / "result.json").write_text(
        json.dumps({"n_total_trials": 1, "finished_at": "2026-10-13T07:00:00Z"})
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": TASK,
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": exception,
            }
        )
    )
    if feedback is not None:
        archive = trial / "verifier/compressed_verdicts/run_on_all_samples/results.zip"
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("EvaluationFeedback.json", json.dumps(feedback))
    subprocess.run(
        [sys.executable, str(EXPERIMENT / "tools/eval/summarize.py"), str(tmp_path)],
        check=True,
    )
    return json.loads((tmp_path / "results/summary.json").read_text())


def test_invalid_syntax_is_a_scored_failure_with_real_denominators(tmp_path):
    summary = scored_run(tmp_path, {"syntax_check_feedback": {"has_passed_syntax_checks": False}})
    assert summary["complete"]
    assert summary["passed"] == 0
    assert summary["errors"] == 0
    for value in summary["problems"][0]["buckets"].values():
        assert value["passed"] == 0
        assert value["total"] > 0


@pytest.mark.parametrize("transport", [False, True])
def test_transport_timeout_is_distinguished_from_agent_budget(tmp_path, transport):
    exception = {
        "exception_type": "AgentTimeoutError",
        "exception_message": "Agent execution timed out after 3600.0 seconds",
        "exception_traceback": (
            "TimeoutError: HTTP request timed out after 600 seconds" if transport else ""
        ),
    }
    summary = scored_run(
        tmp_path,
        {"syntax_check_feedback": {"has_passed_syntax_checks": False}},
        exception=exception,
    )
    assert summary["complete"] is not transport
    assert summary["errors"] == int(transport)
    assert summary["problems"][0]["termination"] == "AgentTimeoutError"
    if transport:
        assert summary["problems"][0]["exception_type"] == "ContainerTransportTimeout"


def test_smoke_requires_edit_and_check_not_just_model_calls():
    spec = importlib.util.spec_from_file_location(
        "smoke_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problem = {"agent_actions": 2, "agent_exit_status": "submitted (exit_cost)"}
    summary = {"complete": True, "total": 1, "problems": [problem]}
    with pytest.raises(RuntimeError, match="edit-and-check"):
        module.validate_model_smoke(summary, 1)
    problem.update(agent_source_changed=True, agent_public_checks=1)
    with pytest.raises(RuntimeError, match="healthy checker"):
        module.validate_model_smoke(summary, 1)  # Old health summaries must be re-audited.
    problem["agent_checker_error_steps"] = []
    module.validate_model_smoke(summary, 1)  # A valid zero score can clear the health gate.
    problem["agent_truncated_calls"] = 1
    with pytest.raises(RuntimeError, match="untruncated"):
        module.validate_model_smoke(summary, 1)
    problem["agent_truncated_calls"] = 0
    problem["agent_checker_error_steps"] = [12]
    with pytest.raises(RuntimeError, match="healthy checker"):
        module.validate_model_smoke(summary, 1)


def test_smoke_reports_inactive_task_without_discarding_valid_zeros():
    spec = importlib.util.spec_from_file_location(
        "smoke_rejection", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    good = {
        "task": "edited",
        "reward": 0.0,
        "agent_source_changed": True,
        "agent_public_checks": 2,
        "agent_checker_error_steps": [],
        "agent_truncated_calls": 0,
        "agent_exit_status": "submitted (exit_cost)",
    }
    inactive = {**good, "task": "inactive", "agent_source_changed": False, "agent_public_checks": 0}
    summary = {"complete": True, "total": 2, "problems": [good, inactive]}
    original = json.dumps(summary, sort_keys=True)
    with pytest.raises(module.SmokeGateError) as caught:
        module.validate_model_smoke(summary, 2)
    details = json.loads(str(caught.value).split("healthy checker: ")[1])
    assert details == {"inactive": ["source_not_changed", "no_public_checker_result"]}
    assert json.dumps(summary, sort_keys=True) == original


def test_checkpoint_launcher_reuses_frozen_source_across_restart(tmp_path, monkeypatch):
    import subprocess
    import sys

    root = tmp_path / "repo"
    previous = tmp_path / "previous"
    frozen = tmp_path / "frozen"
    experiment = Path("experiments/verus_specgym_reproduce")
    (frozen / experiment).mkdir(parents=True)
    (frozen / experiment / "checkpointed.yaml").write_text("profiles: {old-profile: {}}\n")
    (root / experiment).mkdir(parents=True)
    (root / experiment / "checkpointed.yaml").write_text("profiles: {new-profile: {}}\n")
    previous.mkdir()
    source = {"snapshot": str(frozen), "source_sha256": "original-source"}
    (previous / "source-snapshot.json").write_text(json.dumps(source))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    code = (EXPERIMENT / "run-checkpointed.sh").read_text().split("<<'PY'\n", 1)[1]
    code = code.rsplit("\nPY", 1)[0]
    argv = ["-", str(root), "retry", "--source-from", str(previous), "--profile", "old-profile"]
    monkeypatch.setattr(sys, "argv", argv)
    exec(compile(code, "launcher", "exec"), {"__name__": "__main__"})
    saved = root / experiment / "output/retry/source-snapshot.json"
    assert json.loads(saved.read_text()) == source
    unit = tmp_path / "config/systemd/user/specgym-retry.service"
    assert "RestartPreventExitStatus=78" in unit.read_text()
    # Bare-ID resume must retain both the original snapshot and profile selection.
    monkeypatch.setattr(sys, "argv", argv[:3])
    exec(compile(code, "launcher", "exec"), {"__name__": "__main__"})
    assert json.loads(saved.read_text()) == source
    assert '"--profile" "old-profile"' in unit.read_text()
    assert '"--inference-timeout-seconds" "7200"' in unit.read_text()
    # Existing cohorts created before this option retain their original requests.
    settings_path = saved.parent / "launcher.json"
    settings = json.loads(settings_path.read_text())
    settings.pop("inference_timeout_seconds")
    settings_path.write_text(json.dumps(settings))
    unit.write_text(unit.read_text().replace(' "--inference-timeout-seconds" "7200"', ""))
    exec(compile(code, "launcher", "exec"), {"__name__": "__main__"})
    assert "--inference-timeout-seconds" not in unit.read_text()


@pytest.mark.parametrize("smoke", [True, False])
def test_inference_timeout_preserves_agent_budgets_and_changes_request_identity(smoke):
    import dataclasses

    from runners.interface import request_digest

    spec = importlib.util.spec_from_file_location(
        "inference_timeout_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = argparse.Namespace(
        suite="early",
        model_cache=Path("/scratch/models"),
        current_id="inference-timeout-test",
        runtime_config=EXPERIMENT / "runner-checkpoint-bwm48.yaml",
        connection=EXPERIMENT.parents[1] / "src/runners/unityhpc/connections/specgym-bwm48.yaml",
    )
    original = module.request(args, "qwen3.5-9b", [TASK], smoke=smoke)
    args.inference_timeout_seconds = 7200
    repaired = module.request(args, "qwen3.5-9b", [TASK], smoke=smoke)
    assert request_digest(original) != request_digest(repaired)
    job = json.loads(repaired.environment["EVAL_JOB_CONFIG"])
    assert job["agents"][0]["kwargs"]["completion_kwargs"].pop("timeout") == 7200
    assert job == json.loads(original.environment["EVAL_JOB_CONFIG"])
    normalized = dataclasses.replace(repaired, environment=original.environment)
    assert normalized == original
    assert {k: v for k, v in repaired.environment.items() if k != "EVAL_JOB_CONFIG"} == {
        k: v for k, v in original.environment.items() if k != "EVAL_JOB_CONFIG"
    }


def test_public_checker_crash_is_visible_without_changing_benchmark_score(tmp_path):
    trial = tmp_path / "logs/job" / TASK / "agent"
    trial.mkdir(parents=True)
    (trial / "swe-agent.trajectory.json").write_text(
        json.dumps(
            {
                "info": {"exit_status": "submitted (exit_cost)"},
                "trajectory": [
                    {
                        "action": "verus_gym_specgen_check",
                        "observation": "Traceback (most recent call last):\nValidationError",
                    }
                ],
            }
        )
    )
    summary = scored_run(tmp_path, {"syntax_check_feedback": {"has_passed_syntax_checks": False}})
    assert summary["complete"]
    assert summary["passed"] == 0
    assert summary["problems"][0]["agent_checker_error_steps"] == [0]


def test_public_checker_survives_deleted_details_and_preserves_explicit_policy(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "sample_checker", EXPERIMENT / "sandbox/sample_checker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    detailed = tmp_path / "details"
    compressed = tmp_path / "archives"
    (compressed / "1").mkdir(parents=True)
    (detailed / "2").mkdir(parents=True)
    arguments = [
        f"--PARENT_OF_DETAILED_SAVE_DIR={detailed}",
        "--PARENT_OF_COMPRESSED_SAVE_DIR",
        str(compressed),
    ]
    assert module.checker_arguments(arguments)[-1] == "--run_id=3"
    (detailed / "2").rmdir()
    assert module.checker_arguments(arguments)[-1] == "--run_id=2"
    for flags in (
        ["--run_id=explicit"],
        ["--run_id", "explicit"],
        ["--evaluate_sample_only=false"],
        ["--config", "config.yaml"],
    ):
        assert module.checker_arguments(arguments + flags) == arguments + flags


@pytest.mark.parametrize("failure", ["quota", "allocated", "previously_allocated", "other"])
def test_audit_queue_fallback_requires_rejection_before_allocation(tmp_path, monkeypatch, failure):
    monkeypatch.syspath_prepend(str(EXPERIMENT / "tools"))
    spec = importlib.util.spec_from_file_location("audit_queue", EXPERIMENT / "tools/audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {
        "state": "failed",
        "tasks": [
            {
                "state": "rejected",
                "job_id": "123" if failure == "allocated" else None,
                "error": "unrelated" if failure == "other" else "QOSMaxSubmitJobPerUserLimit",
            }
        ],
        "attempts": [{"job_id": "122"}] if failure == "previously_allocated" else [],
    }
    runner = argparse.Namespace(config={"qos": "short"}, status=lambda _: state)
    monkeypatch.setattr(module, "load_runner", lambda *args: runner)
    root = EXPERIMENT.parents[1]
    options = argparse.Namespace(
        suite="early",
        runtime_config=EXPERIMENT / "runner-audit-a100.yaml",
        connection=root / "src/runners/unityhpc/connections/short.yaml",
        model_cache=Path("/models"),
        current_id="audit-queue",
    )
    calls = []

    def collect(runner, request, job, output):
        calls.append((request, output))
        if len(calls) == 1:
            raise module.IncompleteEvaluationError("no archive")
        return {"complete": True}

    monkeypatch.setattr(module, "run_and_collect", collect)
    if failure != "quota":
        with pytest.raises(module.IncompleteEvaluationError):
            module.run_model_diagnostic(root, options, [TASK], smoke=False, output=tmp_path)
        assert len(calls) == 1
        assert not (tmp_path / "queue-fallback.json").exists()
        return
    assert module.run_model_diagnostic(root, options, [TASK], smoke=False, output=tmp_path) == {
        "complete": True
    }
    original, fallback = [c[0] for c in calls]
    assert original.id == "audit-queue"
    assert fallback.id == "audit-queue-qn"
    assert original.hardware == fallback.hardware
    assert original.walltime_seconds == fallback.walltime_seconds
    before, after = [json.loads(r.environment["EVAL_JOB_CONFIG"]) for r in (original, fallback)]
    assert before.pop("job_name") == original.id
    assert after.pop("job_name") == fallback.id
    assert before == after
    for key in ("EVAL_AGENT_CONFIG", "EVAL_RUNNER_CONFIG"):
        assert original.environment[key] == fallback.environment[key]
    assert calls[1][1] == tmp_path / "normal-qos"
    assert (tmp_path / "controller-failure.json").is_file()
    assert options.current_id == original.id


@pytest.mark.parametrize(
    "feedback",
    [
        None,
        {
            "syntax_check_feedback": {"has_passed_syntax_checks": True},
            "execution_feedback": {},
        },
    ],
)
def test_missing_or_partial_verification_is_an_error(tmp_path, feedback):
    summary = scored_run(tmp_path, feedback)
    assert not summary["complete"]
    assert summary["errors"] == 1


def test_inconsistent_reward_is_an_error(tmp_path):
    summary = scored_run(
        tmp_path, {"syntax_check_feedback": {"has_passed_syntax_checks": False}}, 1
    )
    assert not summary["complete"]
    assert summary["problems"][0]["exception_type"] == "RewardFeedbackMismatch"


def test_all_four_buckets_are_required_to_pass(tmp_path):
    manifest = json.loads((EXPERIMENT / "tasks/manifest.json").read_text())
    counts = next(t["bucket_totals"] for t in manifest["tasks"] if t["task"] == TASK)
    feedback = {
        "syntax_check_feedback": {"has_passed_syntax_checks": True},
        "execution_feedback": {
            b: {
                str(i): {
                    "does_testcase_have_expected_verdict": True,
                    "expected_verdict": "sound" if b.endswith("sound") else "complete",
                    "symbolic_verdict": "sound" if b.endswith("sound") else "complete",
                }
                for i in range(counts[b])
            }
            for b in BUCKETS
        },
    }
    summary = scored_run(tmp_path, feedback, 1)
    assert summary["complete"]
    assert summary["passed"] == 1


@pytest.mark.parametrize("status", ["exit_api", "submitted (exit_api)"])
def test_swe_api_failure_is_not_a_valid_benchmark_zero(tmp_path, status):
    trial = tmp_path / "logs/job" / TASK / "agent"
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "swe-agent.trajectory.json").write_text(
        json.dumps({"info": {"exit_status": status}, "trajectory": []})
    )
    summary = scored_run(tmp_path, {"syntax_check_feedback": {"has_passed_syntax_checks": False}})
    assert not summary["complete"]
    assert summary["problems"][0]["exception_type"] == "SweAgentInfrastructureError"


def test_hypergeometric_budget_boundaries():
    spec = importlib.util.spec_from_file_location(
        "specgym_plots", EXPERIMENT / "tools/eval/plots.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    probability = module.sample_pass_probability
    assert probability(3, 5, 2) == pytest.approx(3 / 10)
    assert probability(3, 5, 0) == 1
    assert probability(3, 5, 5) == 0
    assert probability(5, 5, 200) == 1
    with pytest.raises(ValueError):
        probability(6, 5, 2)


def test_parallel_shards_bound_concurrency_and_keep_failures(tmp_path, monkeypatch):
    import threading
    import time

    spec = importlib.util.spec_from_file_location(
        "parallel_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = [{"id": str(i)} for i in range(6)]
    state = {"shards": records.copy()}
    active = peak = fast_active = fast_peak = 0
    seen = []
    lock = threading.Lock()

    def run(args, runner, record):
        nonlocal active, peak, fast_active, fast_peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(record["id"])
            fast_active += record["connection"] == "short.yaml"
            fast_peak = max(fast_peak, fast_active)
        time.sleep(0.02)
        with lock:
            active -= 1
            fast_active -= record["connection"] == "short.yaml"
        return {**record, **({"failure": "infrastructure error"} if record["id"] == "0" else {})}

    monkeypatch.setattr(module, "run_shard", run)
    args = argparse.Namespace(
        parallel_shards=2, connection=Path("normal.yaml"), fast_connection=Path("short.yaml")
    )
    path = tmp_path / "campaign.json"
    with pytest.raises(RuntimeError, match="Unresolved shards"):
        module.parallel_shards(args, None, records, state, path)
    assert peak == 2
    assert fast_peak == 1
    assert sorted(seen) == [str(i) for i in range(6)]
    assert json.loads(path.read_text())["shards"][0]["failure"] == "infrastructure error"


def test_shard_retries_infrastructure_without_repeating_valid_zero(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "retry_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []
    monkeypatch.setattr(
        module, "request", lambda args, *a, **kw: argparse.Namespace(id=args.current_id)
    )

    def collect(runner, request, job, output):
        seen.append(request.id)
        if len(seen) == 1:
            raise module.IncompleteEvaluationError("container failed to start")
        return {"passed": 0, "errors": 0}

    monkeypatch.setattr(module, "run_and_collect", collect)
    args = argparse.Namespace(shard_retries=2, action="run")
    record = {"id": "shard", "model": "qwen3.5-9b", "tasks": [TASK], "output": str(tmp_path)}
    result = module.run_shard(args, None, record)
    assert seen == ["shard", "shard-r1"]
    assert result["id"] == "shard-r1"
    assert len(result["infrastructure_attempts"]) == 1
    assert Path(result["output"]).parent == tmp_path.parent
    seen.clear()
    monkeypatch.setattr(module, "run_and_collect", lambda r, req, *a: seen.append(req.id))
    runner = argparse.Namespace(status=lambda identity: None)
    args.action = "collect"
    collected = module.run_shard(args, runner, result)
    assert seen == ["shard-r1"]
    assert collected["id"] == "shard-r1"


def test_task_checkpoints_resume_partial_shards_without_repeating_valid_zeros(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "checkpoint_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    other = "verus-gym-1005-a"
    args = argparse.Namespace(
        suite="early",
        model_cache=Path("/models"),
        task_checkpoints=True,
        runtime_config=EXPERIMENT / "runner-checkpoint-a100.yaml",
        connection=EXPERIMENT.parents[1]
        / "src/runners/unityhpc/connections/specgym-a100-parallel.yaml",
        shard_retries=0,
        action="run",
    )
    record = {"id": "cp", "model": "qwen3.5-9b", "tasks": [TASK, other], "output": str(tmp_path)}
    seen = []

    def collect(runner, req, job, output):
        settings = json.loads(req.environment["EVAL_JOB_CONFIG"])
        tasks = settings["datasets"][0]["task_names"]
        seen.append((tasks, settings["agents"][0], req.max_attempts))
        results = output / "results"
        results.mkdir(parents=True)
        (results / "evaluation-manifest.json").write_text(
            json.dumps({"hardware_verified": True, "source_sha256": "frozen"})
        )
        problems = []
        for name in tasks:
            failed = len(seen) == 1 and name == other
            trial = output / "logs" / name
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "finished_at": "2026-10-23T12:00:00Z",
                        "verifier_result": {"rewards": {"reward": 0}},
                    }
                )
            )
            problems.append(
                {
                    "task": name,
                    "trial": name,
                    "reward": 0,
                    "passed": False,
                    "exception_type": "ContainerTransportTimeout" if failed else None,
                }
            )
        summary = {"audit_schema": 2, "problems": problems}
        (results / "summary.json").write_text(json.dumps(summary))
        if len(seen) == 1:
            raise module.IncompleteEvaluationError("one interrupted task")
        return summary

    monkeypatch.setattr(module, "run_and_collect", collect)
    interrupted = module.run_shard(args, None, record)
    assert interrupted["failure"]
    assert list(json.loads((tmp_path / "task-checkpoints.json").read_text())["completed"]) == [TASK]
    args.shard_retries = 2
    completed = module.run_shard(args, None, interrupted)
    assert "failure" not in completed
    assert [x[0] for x in seen] == [[TASK, other], [other]]
    assert seen[0][1] == seen[1][1]  # Native agent configuration and budgets are unchanged.
    assert [x[2] for x in seen] == [1, 1]  # No opaque scheduler replay of completed tasks.
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    assert summary["complete"] and summary["total"] == 2 and summary["passed"] == 0
    assert module.run_shard(args, None, completed) == completed
    assert len(seen) == 2  # Restarting the driver reuses even zero-scoring checkpoints.
    args.model_cache = Path("/different-models")
    with pytest.raises(RuntimeError, match="configuration changed"):
        module.run_shard(args, None, completed)


def test_preinstalled_agent_factory_keeps_native_execution_and_validates_runtime(
    tmp_path, monkeypatch
):
    import asyncio

    from harbor.agents.factory import AgentFactory
    from harbor.models.trial.config import AgentConfig

    from agentic_eval.preinstalled_swe_agent import PreinstalledSweAgent

    config = AgentConfig(
        import_path="agentic_eval.preinstalled_swe_agent:PreinstalledSweAgent",
        model_name="hosted_vllm/specgym",
        kwargs={"version": "a" * 40},
    )
    agent = AgentFactory.create_agent_from_config(config, logs_dir=tmp_path)
    assert isinstance(agent, PreinstalledSweAgent)
    commands = []

    async def execute(environment, command):
        commands.append(command)

    monkeypatch.setattr(agent, "exec_as_root", execute)
    asyncio.run(agent.install(None))
    assert len(commands) == 1 and "agent-preflight.py" in commands[0]
    assert "a" * 40 in commands[0]
    assert "pip install" not in commands[0]


@pytest.mark.parametrize(
    "suite,model,gpus,context,output_tokens",
    [
        ("paper", "kimi-k2.6", 8, 131072, 16384),
        ("early", "qwen3.5-9b", 1, 32768, 8192),
    ],
)
def test_campaign_keeps_early_and_paper_execution_budgets_separate(
    suite, model, gpus, context, output_tokens
):
    spec = importlib.util.spec_from_file_location(
        "specgym_campaign", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = argparse.Namespace(
        suite=suite,
        model_cache=Path("/scratch/models"),
        current_id="budget-test",
        runtime_config=EXPERIMENT / ("runner.yaml" if suite == "paper" else "runner-early.yaml"),
        connection=EXPERIMENT.parents[1] / "src/runners/unityhpc/connections/verus-specgym.yaml",
    )
    request = module.request(args, model, [TASK], smoke=False)
    job = json.loads(request.environment["EVAL_JOB_CONFIG"])
    runtime = json.loads(request.environment["EVAL_RUNNER_CONFIG"])
    assert request.hardware["gpu_count"] == gpus
    assert runtime["model_server"]["context_length"] == context
    assert int(job["agents"][0]["kwargs"]["max_input_tokens"]) + output_tokens == context
    assert job["agents"][0]["kwargs"]["completion_kwargs"]["max_tokens"] == output_tokens
    assert job["datasets"][0]["task_names"] == [TASK]


@pytest.mark.parametrize(
    "profile,precision,concurrent,context",
    [("l40s", "bfloat16", 2, 32768), ("r8k", "float16", 2, 32768), ("l4", "bfloat16", 1, 8192)],
)
def test_single_gpu_smoke_preserves_variant_limits(profile, precision, concurrent, context):
    import shlex

    spec = importlib.util.spec_from_file_location(
        "specgym_variant", EXPERIMENT / "tools/campaign.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = argparse.Namespace(
        suite="early",
        model_cache=Path("/scratch/models"),
        current_id="variant-test",
        runtime_config=EXPERIMENT / f"runner-single-{profile}.yaml",
        connection=EXPERIMENT.parents[1]
        / f"src/runners/unityhpc/connections/specgym-{profile}.yaml",
    )
    req = module.request(args, "qwen3.5-9b", [TASK, "verus-gym-1005-a"], smoke=True)
    runtime = json.loads(req.environment["EVAL_RUNNER_CONFIG"])
    job = json.loads(req.environment["EVAL_JOB_CONFIG"])
    command = shlex.split(runtime["model_server"]["command"])
    assert command[command.index("--dtype") + 1] == precision
    assert job["n_concurrent_trials"] == concurrent
    assert runtime["model_server"]["context_length"] == context
    assert (
        int(job["agents"][0]["kwargs"]["max_input_tokens"])
        + int(job["agents"][0]["kwargs"]["completion_kwargs"]["max_tokens"])
        == context
    )
