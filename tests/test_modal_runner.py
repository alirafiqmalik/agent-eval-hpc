import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from agentic_eval.cli import build_request
from agentic_eval.jobs import validate_experiment
from agentic_eval.launch import harbor_command
from runners.budget import Budget
from runners.discovery import load_runner
from runners.interface import ExecutionConflictError, ExecutionNotFoundError
from runners.modalcloud.adapter import ModalRunner, build_bundle, load_runner_yaml
from runners.modalcloud.environment import ModalRunEnvironment
from runners.modalcloud.jobs.task import gpu_attestation, server_command

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "src/runners/modalcloud/connections/config.yaml"
JOB = ROOT / "experiments/modal-starter-experiment/job.yaml"
UNLIMITED = {"gpu_hours": None, "cpu_hours": None}


class FakeModal:
    """The ModalAPI surface, kept in memory."""

    def __init__(self):
        self.records, self.files, self.spawns, self.states = {}, {}, [], {}
        self.deploys = 0
        self.cancelled = None

    def get(self, identity):
        return copy.deepcopy(self.records.get(identity))

    def claim(self, identity, record):
        if identity in self.records:
            return False
        self.records[identity] = copy.deepcopy(record)
        return True

    def put(self, identity, record):
        self.records[identity] = copy.deepcopy(record)

    def upload(self, files):
        self.files.update(files)

    def read(self, path):
        return self.files.get(path)

    def deploy(self):
        self.deploys += 1

    def spawn(self, options, arguments):
        self.spawns.append((options, arguments))
        return f"fc-{len(self.spawns)}"

    def call_state(self, call_id):
        return self.states.get(call_id, "running")

    def cancel(self, call_id, tags):
        self.cancelled = (call_id, tags)
        return 0


def _runner(tmp_path, fake=None):
    fake = fake or FakeModal()
    budget = Budget(tmp_path / "budget.json", **UNLIMITED)
    return ModalRunner(CONFIG, budget, fake), fake


def _finish(fake, run_id, exit_code=0, files=None):
    files = files or {"harbor-jobs.tar.gz": b"archive", ".controller/hardware-1.json": b"{}"}
    for name, data in files.items():
        fake.files[f"runs/{run_id}/output/{name}"] = data
    record = {
        "exit_code": exit_code,
        "started_at": 100.0,
        "finished_at": 160.0,
        "elapsed_seconds": 60,
        "function_call_id": "fc-1",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "missing": [],
    }
    fake.files[f"runs/{run_id}/output/exit.json"] = json.dumps(record).encode()


def test_registry_loads_the_modal_runner():
    assert isinstance(load_runner(ROOT, "modal", CONFIG), ModalRunner)


def test_request_uses_the_experiment_model_server_gpu(tmp_path):
    runner, _ = _runner(tmp_path)
    request = build_request(runner, ROOT, JOB, "modal-001", CONFIG, None)
    assert request.argv == (
        "python",
        "src/runners/modalcloud/jobs/task.py",
        "experiments/modal-starter-experiment/job.yaml",
    )
    assert request.hardware["gpu_count"] == 1
    assert request.hardware["gpu_type"] == "A10G"
    assert request.environment["EVAL_MODAL_APP"] == "agentic-eval"
    assert request.walltime_seconds == 3600


def test_bundle_is_byte_stable_and_holds_only_the_project_and_experiment(tmp_path):
    import io
    import tarfile

    first, digest, count = build_bundle(ROOT, JOB)
    second, again, _ = build_bundle(ROOT, JOB)
    assert first == second and digest == again
    names = tarfile.open(fileobj=io.BytesIO(first)).getnames()
    assert len(names) == count
    assert "experiments/modal-starter-experiment/job.yaml" in names
    assert "src/runners/modalcloud/jobs/task.py" in names
    assert "uv.lock" in names
    assert not any(name.startswith("experiments/unityhpc-starter-experiment") for name in names)
    assert not any("/output/" in name or "__pycache__" in name for name in names)


def test_bundle_rejects_a_dataset_outside_the_experiment(tmp_path):
    job = ROOT / "experiments/modal-starter-experiment/outside.yaml"
    config = yaml.safe_load(JOB.read_text())
    config["datasets"] = [{"path": "experiments/unityhpc-starter-experiment/tasks/humaneval"}]
    job.write_text(yaml.safe_dump(config))
    try:
        with pytest.raises(ValueError, match="only the experiment folder"):
            build_bundle(ROOT, job)
    finally:
        job.unlink()


def test_submit_is_idempotent_and_rejects_a_changed_request(tmp_path):
    runner, fake = _runner(tmp_path)
    request = runner.request_for(ROOT, JOB, "modal-001")
    assert runner.submit(request)["state"] == "running"
    assert runner.submit(request)["state"] == "running"
    assert len(fake.spawns) == 1
    options, arguments = fake.spawns[0]
    assert options == {"cpu": 2.0, "memory": 4096, "timeout": 3600 + 600, "retries": 0}
    assert arguments["argv"] == list(request.argv)
    assert "runs/modal-001/bundle.tar.gz" in fake.files
    changed = runner.request_for(ROOT, JOB, "modal-001", walltime_seconds=1200)
    with pytest.raises(ExecutionConflictError):
        runner.submit(changed)
    assert len(fake.spawns) == 1


def test_interrupted_submit_spawns_once_on_retry(tmp_path):
    runner, fake = _runner(tmp_path)
    request = runner.request_for(ROOT, JOB, "modal-001")
    fake.spawn = lambda options, arguments: (_ for _ in ()).throw(ConnectionError("lost"))
    with pytest.raises(ConnectionError):
        runner.submit(request)
    assert "call_id" not in fake.records["modal-001"]
    del fake.spawn
    runner.submit(request)
    assert fake.records["modal-001"]["call_id"] == "fc-1"
    assert len(fake.spawns) == 1


@pytest.mark.parametrize(
    "exit_code,cancelled,state",
    [(0, False, "completed"), (1, False, "failed"), (1, True, "cancelled"), (0, True, "completed")],
)
def test_status_reads_the_exit_record(tmp_path, exit_code, cancelled, state):
    runner, fake = _runner(tmp_path)
    runner.submit(runner.request_for(ROOT, JOB, "modal-001"))
    if cancelled:
        fake.records["modal-001"]["cancelled"] = True
    _finish(fake, "modal-001", exit_code)
    status = runner.status("modal-001")
    assert status["state"] == state
    assert status["tasks"][0]["job_id"] == "fc-1"


def test_status_of_an_unknown_run_is_not_found(tmp_path):
    runner, _ = _runner(tmp_path)
    with pytest.raises(ExecutionNotFoundError):
        runner.status("absent")


def test_cancel_marks_the_run_and_stops_its_tagged_sandboxes(tmp_path):
    runner, fake = _runner(tmp_path)
    runner.submit(runner.request_for(ROOT, JOB, "modal-001"))
    fake.states["fc-1"] = "failed"
    assert runner.cancel("modal-001")["state"] == "cancelled"
    assert fake.cancelled == ("fc-1", {"agentic-eval.run": "modal-001"})


def test_attest_matches_only_the_recorded_request_and_source(tmp_path):
    runner, _ = _runner(tmp_path)
    request = runner.request_for(ROOT, JOB, "modal-001")
    observation = runner.submit(request)
    assert runner.attest(request, observation)["matched"] is True
    other = runner.request_for(ROOT, JOB, "modal-001", walltime_seconds=1200)
    assert runner.attest(other, observation)["matched"] is False


def test_fetch_verifies_digests_and_settles_the_budget(tmp_path):
    runner, fake = _runner(tmp_path)
    runner.submit(runner.request_for(ROOT, JOB, "modal-001"))
    _finish(fake, "modal-001")
    receipts = runner.fetch("modal-001", tmp_path / "out")
    assert sorted(r["path"] for r in receipts) == [
        ".controller/hardware-1.json",
        "harbor-jobs.tar.gz",
    ]
    assert (tmp_path / "out/evaluation/harbor-jobs.tar.gz").read_bytes() == b"archive"
    ledger = json.loads((tmp_path / "budget.json").read_text())["modal-001"]
    assert ledger["gpu_seconds"] == 60 and ledger["settlement"]["evidence"]["backend"] == "modal"
    fake.files["runs/modal-001/output/harbor-jobs.tar.gz"] = b"changed"
    with pytest.raises(ValueError, match="differs"):
        runner.fetch("modal-001", tmp_path / "again")


def test_fetch_before_exit_returns_nothing(tmp_path):
    runner, _ = _runner(tmp_path)
    runner.submit(runner.request_for(ROOT, JOB, "modal-001"))
    assert runner.fetch("modal-001", tmp_path / "out") == []


def test_plan_rejects_walltime_beyond_the_modal_limit(tmp_path):
    runner, _ = _runner(tmp_path)
    request = runner.request_for(ROOT, JOB, "modal-001", walltime_seconds=90000)
    with pytest.raises(ValueError, match="Modal limit"):
        runner.plan(request)


def test_runner_yaml_requires_command_placeholders(tmp_path):
    runner = yaml.safe_load((JOB.parent / "runner.yaml").read_text())
    runner["model_server"]["command"] = "vllm serve fixed-model"
    (tmp_path / "runner.yaml").write_text(yaml.safe_dump(runner))
    with pytest.raises(ValueError, match="placeholders|must include"):
        load_runner_yaml(tmp_path)


SERVER = {
    "gpu": "A10G",
    "gpu_count": 1,
    "gpu_names": ["NVIDIA A10G", "NVIDIA A10"],
    "gpu_memory_mb": 23028,
    "gpu_memory_tolerance_mb": 512,
}


@pytest.mark.parametrize(
    "rows,verified",
    [
        (["NVIDIA A10, 23028"], True),
        (["NVIDIA A10G, 23028"], True),
        (["NVIDIA L4, 23034"], False),
        (["NVIDIA A10G, 16000"], False),
        (["NVIDIA A10G, 23028", "NVIDIA A10G, 23028"], False),
        ([], False),
    ],
)
def test_model_server_gpu_attestation(rows, verified):
    assert gpu_attestation(rows, SERVER)["verified"] is verified


def test_modal_environment_skips_token_preflight_inside_a_function(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.setenv("MODAL_IS_REMOTE", "1")
    ModalRunEnvironment.preflight()
    monkeypatch.delenv("MODAL_IS_REMOTE")
    with pytest.raises(SystemExit):
        ModalRunEnvironment.preflight()


def test_modal_job_gets_no_singularity_resolver_mount(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic_eval.launch.shutil.which", lambda name: "/bin/harbor")
    modal = harbor_command(JOB, tmp_path, None)
    assert modal[modal.index("--env") + 1] == ("runners.modalcloud.environment:ModalRunEnvironment")
    assert "--mounts" not in modal
    assert "--ak" not in modal
    unity = harbor_command(
        ROOT / "experiments/unityhpc-starter-experiment/job.yaml", tmp_path, None
    )
    assert "/etc/resolv.conf" in unity[unity.index("--mounts") + 1]


def test_modal_experiment_uses_a_dockerfile_sandbox():
    validate_experiment(JOB)
    tasks = sorted(p.parent.name for p in (JOB.parent / "tasks/starter").glob("*/task.toml"))
    assert tasks == ["HumanEval_2", "HumanEval_23", "cpu-probe", "gpu-probe"]
    server = yaml.safe_load((JOB.parent / "runner.yaml").read_text())["model_server"]
    gpu_task = (JOB.parent / "tasks/starter/gpu-probe/task.toml").read_text()
    assert f'gpu_types = ["{server["gpu"]}"]' in gpu_task


def test_server_command_keeps_json_flag_values():
    server = yaml.safe_load((JOB.parent / "runner.yaml").read_text())["model_server"]
    command = server_command(server, "Qwen/Qwen3-0.6B")
    assert command.startswith("vllm serve Qwen/Qwen3-0.6B ")
    assert "--port 8000" in command and "--max-model-len 32768" in command
    assert "'{\"enable_thinking\": false}'" in command
    assert "{model}" not in command
