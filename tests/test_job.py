import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harbor.models.job.config import JobConfig

from agentic_eval.launch import harbor_command, stage_job
from agentic_eval.unity import request_for

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "experiments/unityhpc-starter-experiment/job.yaml"
RUNNER = ROOT / "src/runners/unityhpc/connections/config.yaml"
EXPERIMENT = JOB.parent


def test_starter_job_is_a_harbor_config():
    job = JobConfig.model_validate(yaml.safe_load(JOB.read_text()))
    assert job.job_name == "unityhpc-starter-experiment"
    assert job.agents[0].name == "qwen-coder"
    model = yaml.safe_load((EXPERIMENT / "agent/agent-config.yaml").read_text())["model"]
    assert job.agents[0].model_name == model
    smoke = JobConfig.model_validate(yaml.safe_load((EXPERIMENT / "smoke.yaml").read_text()))
    assert smoke.agents[0].model_name == model
    assert job.n_attempts == 1
    assert job.n_concurrent_trials == 4
    assert job.datasets[0].path.as_posix().endswith("tasks/humaneval")
    tasks = list((EXPERIMENT / "tasks/humaneval").glob("HumanEval_*/task.toml"))
    assert len(tasks) == 164
    sample = EXPERIMENT / "tasks/humaneval/HumanEval_0/instruction.md"
    assert "canonical_solution" not in sample.read_text()
    assert (EXPERIMENT / "tasks/humaneval/HumanEval_0/tests/test.sh").is_file()


def test_unity_request_uses_the_experiment_gpu():
    request = request_for(ROOT, JOB, "starter-001", RUNNER)
    assert request.bundle == ROOT
    assert request.argv == (
        "bash",
        "src/runners/unityhpc/jobs/task.sh",
        "experiments/unityhpc-starter-experiment/job.yaml",
    )
    assert request.outputs == ("harbor-jobs.tar.gz",)
    assert request.hardware["gpu_count"] == 1
    assert request.hardware["gpu_name"] == "NVIDIA A100-SXM4-80GB"
    assert request.hardware["gpu_memory_mb"] == 81920
    assert request.walltime_seconds == 14400


def test_smoke_run_can_request_a_shorter_walltime():
    request = request_for(ROOT, EXPERIMENT / "smoke.yaml", "smoke-001", RUNNER, 3600)
    assert request.walltime_seconds == 3600


def test_agent_version_is_pinned_in_harbor_command(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_eval.launch.shutil.which", lambda name: "/bin/harbor")
    command = harbor_command(JOB, tmp_path, "http://127.0.0.1:8000/v1")
    assert "version=0.25.1" in command
    assert "QWEN_CODE_MAX_OUTPUT_TOKENS=2048" in command
    assert "--mounts" in command
    assert json.loads(command[command.index("--mounts") + 1])[0]["target"] == "/etc/resolv.conf"


def test_qwen_coder_uses_private_endpoint_credentials(tmp_path, monkeypatch):
    from harbor.agents.installed.qwen_code import QwenCode

    monkeypatch.setattr("agentic_eval.launch.shutil.which", lambda name: "/bin/harbor")
    monkeypatch.setenv("OPENAI_API_KEY", "test-allocation-secret")
    command = harbor_command(JOB, tmp_path, "http://127.0.0.1:39123/v1")
    kwargs = dict(
        command[index + 1].split("=", 1)
        for index, argument in enumerate(command[:-1])
        if argument == "--ak"
    )
    options = QwenCode.parse_options(kwargs)
    assert options.api_key == "test-allocation-secret"
    assert options.base_url == "http://127.0.0.1:39123/v1"
    assert "api_key" not in kwargs
    assert "test-allocation-secret" not in " ".join(command)
    staged = stage_job(EXPERIMENT / "smoke.yaml", tmp_path / "staged", tmp_path / "image.sif")
    assert "test-allocation-secret" not in staged.read_text()


def test_harbor_preserves_configured_environment_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_eval.launch.shutil.which", lambda name: "/bin/harbor")
    job = ROOT / "experiments/verus_specgym_reproduce/job.yaml"
    command = harbor_command(job, tmp_path, None)
    assert command[command.index("--env") + 1] == (
        "runners.unityhpc.environment:UnitySingularityEnvironment"
    )


def test_singularity_command_budget_preserves_explicit_timeouts(monkeypatch):
    import asyncio

    from harbor.environments.singularity import SingularityEnvironment

    from runners.unityhpc.environment import UnitySingularityEnvironment

    seen = []

    async def execute(self, **kwargs):
        seen.append(kwargs)
        return "done"

    monkeypatch.setattr(SingularityEnvironment, "exec", execute)
    env = object.__new__(UnitySingularityEnvironment)
    env.default_exec_timeout_sec = 7200
    assert asyncio.run(env.exec("long agent", env={"A": "B"})) == "done"
    asyncio.run(env.exec("short tool", timeout_sec=30))
    assert [item["timeout_sec"] for item in seen] == [7200, 30]
    assert seen[0]["env"] == {"A": "B"}


def test_staged_job_uses_writable_image_and_preserves_source(tmp_path):
    image = tmp_path / "sandbox.sif"
    staged = stage_job(EXPERIMENT / "smoke.yaml", tmp_path / "staged", image)
    data = yaml.safe_load(staged.read_text())
    dataset = Path(data["datasets"][0]["path"])
    task = dataset / "HumanEval_0/task.toml"
    assert f'docker_image = "{image}"' in task.read_text()
    assert "cpus = 2" in task.read_text()
    assert (
        "experiments/unityhpc-starter-experiment/sandbox/sandbox.sif"
        in (EXPERIMENT / "tasks/humaneval/HumanEval_0/task.toml").read_text()
    )


def test_staging_can_patch_read_only_controller_source(tmp_path):
    root = tmp_path / "source"
    task = root / "experiments/sample/tasks/example"
    task.mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    original = task / "task.toml"
    original.write_text('[environment]\ndocker_image = "sandbox.sif"\n')
    original.chmod(0o444)
    job = root / "experiments/sample/job.yaml"
    job.write_text("datasets:\n  - path: experiments/sample/tasks\n")
    staged = stage_job(job, tmp_path / "output", tmp_path / "sandbox.sif")
    staged_task = Path(yaml.safe_load(staged.read_text())["datasets"][0]["path"])
    assert (
        f'docker_image = "{tmp_path / "sandbox.sif"}"'
        in (staged_task / "example/task.toml").read_text()
    )


def test_harbor_shard_override_copies_only_selected_tasks(tmp_path, monkeypatch):
    config = yaml.safe_load(JOB.read_text())
    config["datasets"][0]["task_names"] = ["HumanEval_1"]
    monkeypatch.setenv("EVAL_JOB_CONFIG", json.dumps(config))
    staged = stage_job(JOB, tmp_path / "staged", tmp_path / "image.sif")
    dataset = Path(yaml.safe_load(staged.read_text())["datasets"][0]["path"])
    assert [p.name for p in dataset.iterdir()] == ["HumanEval_1"]
    config["datasets"][0]["task_names"] = ["../agent"]
    monkeypatch.setenv("EVAL_JOB_CONFIG", json.dumps(config))
    with pytest.raises(ValueError, match="invalid task name"):
        stage_job(JOB, tmp_path / "unsafe", tmp_path / "image.sif")


def test_swe_agent_uses_environment_for_model_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_eval.launch.shutil.which", lambda name: "/bin/harbor")
    job = ROOT / "experiments/verus_specgym_reproduce/job.yaml"
    command = harbor_command(job, tmp_path, "http://127.0.0.1:8000/v1")
    assert not any(arg.startswith(("base_url=", "api_key=")) for arg in command)


def test_hosted_vllm_key_reaches_agent_without_serializing_secret(tmp_path, monkeypatch):
    from harbor.utils.env import resolve_env_vars

    job = ROOT / "experiments/verus_specgym_reproduce/job.yaml"
    config = yaml.safe_load(job.read_text())
    config["datasets"][0]["task_names"] = ["verus-gym-1009-a"]
    monkeypatch.setenv("EVAL_JOB_CONFIG", json.dumps(config))
    monkeypatch.setenv("OPENAI_API_KEY", "test-ephemeral-secret")
    staged = stage_job(job, tmp_path / "staged", tmp_path / "image.sif")
    env = yaml.safe_load(staged.read_text())["agents"][0]["env"]
    assert resolve_env_vars(env)["HOSTED_VLLM_API_KEY"] == "test-ephemeral-secret"
    assert "test-ephemeral-secret" not in staged.read_text()


def test_summary_counts_passed_problems(tmp_path):
    job_dir = tmp_path / "logs/job"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps({"n_total_trials": 2, "finished_at": "2026-10-12T00:00:00Z"})
    )
    for name, reward in (("HumanEval_0", "1\n"), ("HumanEval_1", "0\n")):
        reward_dir = tmp_path / "logs" / "job" / name / "verifier"
        reward_dir.mkdir(parents=True)
        (reward_dir / "reward.txt").write_text(reward)
    script = EXPERIMENT / "tools/eval/summarize.py"
    subprocess.check_call([sys.executable, str(script), str(tmp_path)])
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    assert summary["passed"] == 1
    assert summary["total"] == 2
    assert summary["expected_total"] == 2
    assert summary["complete"] is True
    assert summary["errors"] == 0
    assert summary["problems"][0]["trial"] == "job/HumanEval_0"


def test_summary_distinguishes_agent_errors_from_failed_tasks(tmp_path):
    trial = tmp_path / "logs/job/HumanEval_0"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier/reward.txt").write_text("0\n")
    (trial / "result.json").write_text(
        json.dumps({"exception_info": {"exception_type": "UnknownApiError"}})
    )
    script = EXPERIMENT / "tools/eval/summarize.py"
    subprocess.check_call([sys.executable, str(script), str(tmp_path)])
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    assert summary["passed"] == 0
    assert summary["errors"] == 1
    assert summary["problems"][0]["exception_type"] == "UnknownApiError"


def test_summary_includes_trials_without_reward_files(tmp_path):
    trial = tmp_path / "logs/job/HumanEval_0"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "HumanEval_0",
                "exception_info": {"exception_type": "AgentSetupError"},
            }
        )
    )
    script = EXPERIMENT / "tools/eval/summarize.py"
    subprocess.check_call([sys.executable, str(script), str(tmp_path)])
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    assert summary["total"] == 1
    assert summary["errors"] == 1
    assert summary["problems"][0]["reward"] is None


def test_cluster_entrypoint_serves_the_model_then_runs_harbor():
    script = (ROOT / "src/runners/unityhpc/jobs/task.sh").read_text()
    launch = (ROOT / "src/agentic_eval/launch.py").read_text()
    assert "APPTAINER_IGNORE_FAKEROOT_COMMAND=1" in script
    assert "sandbox.sif" in script
    assert "model-server.sh" in script
    assert "agentic_eval.launch" in script
    assert "${output_dir}/logs" in script
    assert "--env" in launch and "singularity" in launch
