import importlib.util
import json
from pathlib import Path
from urllib.error import URLError

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "unity_eval_script", Path(__file__).resolve().parents[1] / "scripts/unity_eval.py"
)
assert _SPEC is not None and _SPEC.loader is not None
unity_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(unity_eval)


class _Runner:
    def fetch(self, run_id: str, output: Path) -> list[dict]:
        return [{"path": "harbor-jobs.tar.gz", "sha256": "test"}]


def test_collect_rejects_missing_trial_even_when_controller_completed(tmp_path, monkeypatch):
    def write_partial_result(experiment: Path, output: Path) -> None:
        (output / "results").mkdir()
        (output / "results/summary.json").write_text(
            json.dumps(
                {"passed": 1, "total": 1, "expected_total": 2, "errors": 0, "complete": False}
            )
        )

    monkeypatch.setattr(unity_eval, "unpack_run", lambda output: None)
    monkeypatch.setattr(unity_eval, "post_run", write_partial_result)
    hardware = tmp_path / "output/evaluation/.controller/hardware-1.json"
    hardware.parent.mkdir(parents=True)
    hardware.write_text('{"verified": true}')
    campaign = {"state": "completed", "tasks": [{"state": "completed", "job_id": "1"}]}

    with pytest.raises(ValueError, match="incomplete evaluation"):
        unity_eval.collect(_Runner(), tmp_path / "job.yaml", "trial", tmp_path / "output", campaign)

    manifest = json.loads((tmp_path / "output/results/evaluation-manifest.json").read_text())
    assert manifest["complete"] is False
    assert manifest["summary"]["expected_total"] == 2


def test_run_submits_then_collects_same_campaign(tmp_path, monkeypatch):
    calls = []

    class Runner:
        def submit(self, request):
            calls.append(("submit", request))

    runner = Runner()
    monkeypatch.setattr(unity_eval, "load_runner", lambda root, name, config: runner)
    monkeypatch.setattr(unity_eval, "validate_experiment", lambda job: None)
    monkeypatch.setattr(unity_eval, "request_for", lambda *args: "request")
    monkeypatch.setattr(
        unity_eval,
        "wait_for_terminal",
        lambda actual, run_id, interval: calls.append(("wait", run_id)) or {"state": "completed"},
    )
    monkeypatch.setattr(
        unity_eval,
        "collect",
        lambda actual, job, run_id, output, campaign: calls.append(("collect", run_id)),
    )

    assert (
        unity_eval.main(
            [
                "run",
                str(tmp_path / "job.yaml"),
                "--run-id",
                "example",
                "--runner-config",
                str(tmp_path / "connection.yaml"),
            ]
        )
        == 0
    )
    assert calls == [("submit", "request"), ("wait", "example"), ("collect", "example")]


def test_wait_recovers_from_temporary_controller_failure(monkeypatch):
    class Runner:
        attempts = 0

        def status(self, run_id):
            self.attempts += 1
            if self.attempts == 1:
                raise URLError("temporary connection failure")
            return {"state": "completed", "tasks": [{"state": "completed"}]}

    monkeypatch.setattr(unity_eval.time, "sleep", lambda seconds: None)
    runner = Runner()
    assert unity_eval.wait_for_terminal(runner, "example", 1)["state"] == "completed"
    assert runner.attempts == 2


def test_submit_retries_uncertain_response_with_same_request(monkeypatch):
    class Runner:
        requests = []

        def submit(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise URLError("response lost after submit")

    class Request:
        id = "example"

    monkeypatch.setattr(unity_eval.time, "sleep", lambda seconds: None)
    runner = Runner()
    request = Request()
    unity_eval.submit_with_retries(runner, request)
    assert runner.requests == [request, request]
