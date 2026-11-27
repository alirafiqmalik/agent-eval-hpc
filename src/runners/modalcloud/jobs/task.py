"""Serve the experiment model in a Modal GPU sandbox, then run Harbor once.

Runs inside the orchestrator Function (see runners/modalcloud/app.py), which
sets EVAL_OUTPUT_DIR, EVAL_RUN_ID, and EVAL_MODAL_APP. The model server is a
separate sandbox with an HTTPS tunnel and a per-run API key, so trial sandboxes
reach it like any OpenAI endpoint.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import modal
import yaml

from runners.modalcloud.environment import RUN_TAG

MODEL_CACHE = "/root/.cache/huggingface"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _request(url: str, key: str, body: dict | None = None, timeout: int = 30) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def gpu_attestation(rows: list[str], server: dict) -> dict:
    """Compare nvidia-smi rows ("name, memory MiB") with the requested GPU."""
    gpus = []
    for row in rows:
        name, _, memory = row.rpartition(",")
        gpus.append({"name": name.strip(), "memory_mb": int(memory.strip())})
    tolerance = server.get("gpu_memory_tolerance_mb", 0)
    verified = len(gpus) == server["gpu_count"] and all(
        gpu["name"] in server["gpu_names"]
        and abs(gpu["memory_mb"] - server["gpu_memory_mb"]) <= tolerance
        for gpu in gpus
    )
    requested = {
        key: server.get(key)
        for key in ("gpu", "gpu_count", "gpu_names", "gpu_memory_mb", "gpu_memory_tolerance_mb")
    }
    return {"verified": verified, "requested": requested, "gpus": gpus}


def served_name(server: dict, model: str) -> str:
    return server.get("served_model_name") or model.rsplit("/", 1)[-1]


def server_command(server: dict, model: str) -> str:
    """Fill the three placeholders. Other braces stay, for JSON flag values."""
    values = {"model": model, "port": server["port"], "context_length": server["context_length"]}
    command = server["command"]
    for name, value in values.items():
        command = command.replace("{" + name + "}", str(value))
    return command


def start_server(app, server: dict, model: str, tags: dict, walltime: int):
    key = secrets.token_urlsafe(32)
    command = server_command(server, model)
    cache = modal.Volume.from_name(os.environ["EVAL_MODAL_MODEL_CACHE"], create_if_missing=True)
    sandbox = modal.Sandbox.create(
        "sh",
        "-c",
        "exec " + command,
        app=app,
        # Server images such as vllm-openai set ENTRYPOINT; run the configured command alone.
        image=modal.Image.from_registry(server["image"]).entrypoint([]),
        gpu=f"{server['gpu']}:{server['gpu_count']}",
        cpu=server["cpus"],
        memory=server["memory_mb"],
        timeout=walltime,
        encrypted_ports=[server["port"]],
        secrets=[modal.Secret.from_dict({"VLLM_API_KEY": key})],
        volumes={MODEL_CACHE: cache},
        tags=tags,
    )
    url = sandbox.tunnels(timeout=server.get("startup_timeout_seconds", 900))[server["port"]].url
    return sandbox, url.rstrip("/"), key


def wait_ready(sandbox, url: str, key: str, name: str, timeout: int) -> dict:
    """Return /v1/models once vLLM lists the served model; fail if the server exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sandbox.poll() is not None:
            raise RuntimeError(f"model server exited with code {sandbox.returncode}")
        try:
            models = _request(f"{url}/v1/models", key, timeout=15)
            if any(entry.get("id") == name for entry in models.get("data", [])):
                return models
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
            pass
        time.sleep(10)
    raise RuntimeError(f"model server did not list {name} within {timeout} seconds")


def _stop(sandbox, log: Path) -> None:
    try:
        sandbox.terminate()
        sandbox.wait(raise_on_termination=False)
        log.write_text(sandbox.stdout.read() + sandbox.stderr.read())
    except Exception as error:  # Keep the run's own result if log capture fails.
        log.write_text(f"model server log unavailable: {error}\n")


def _terminate_tagged(app, tags: dict) -> int:
    count = 0
    for sandbox in modal.Sandbox.list(app_id=app.app_id, tags=tags):
        sandbox.terminate()
        count += 1
    return count


def run(job: Path, output: Path) -> int:
    experiment = job.parent
    agent = yaml.safe_load((experiment / "agent/agent-config.yaml").read_text())
    runner = yaml.safe_load((experiment / "runner.yaml").read_text())
    server = runner["model_server"]
    walltime = runner["walltime_seconds"]
    logs, results = output / "logs", output / "results"
    logs.mkdir(parents=True, exist_ok=True)
    versions = {}
    for package in ("harbor", "modal"):
        versions[package] = importlib.metadata.version(package)
    _write_json(
        results / "runtime.json",
        {"packages": versions, "python": sys.version, "model": agent["model"]},
    )
    app = modal.App.lookup(os.environ["EVAL_MODAL_APP"], create_if_missing=True)
    tags = {RUN_TAG: os.environ["EVAL_RUN_ID"]}
    env = dict(os.environ)
    sandbox = None
    try:
        attestation = {"verified": True, "requested": {"gpu_count": 0}, "gpus": []}
        if server["mode"] == "modal":
            started = time.monotonic()
            sandbox, url, key = start_server(app, server, agent["model"], tags, walltime)
            rows = sandbox.exec(
                "nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"
            )
            rows.wait()
            attestation = gpu_attestation(rows.stdout.read().strip().splitlines(), server)
            attestation["sandbox_id"] = sandbox.object_id
            _write_json(output / ".controller/hardware-1.json", attestation)
            if not attestation["verified"]:
                print(f"model server GPU differs from the request: {attestation}", file=sys.stderr)
                return 1
            name = served_name(server, agent["model"])
            models = wait_ready(sandbox, url, key, name, server.get("startup_timeout_seconds", 900))
            # One generated token proves the weights load and decode before Harbor starts.
            smoke = _request(
                f"{url}/v1/chat/completions",
                key,
                {
                    "model": name,
                    "messages": [{"role": "user", "content": "Say ok."}],
                    "max_tokens": 1,
                },
                timeout=120,
            )
            _write_json(
                results / "model-server.json",
                {
                    "served_model": name,
                    "models": [entry.get("id") for entry in models.get("data", [])],
                    "startup_seconds": round(time.monotonic() - started),
                    "smoke_usage": smoke.get("usage"),
                },
            )
            env.update(OPENAI_BASE_URL=f"{url}/v1", OPENAI_API_KEY=key)
        else:
            _write_json(output / ".controller/hardware-1.json", attestation)
        command = [sys.executable, "-m", "agentic_eval.launch", str(job), "--jobs-dir", str(logs)]
        return subprocess.call(command, env=env)
    finally:
        if sandbox is not None:
            _stop(sandbox, logs / "model-server.log")
        _terminate_tagged(app, tags)
        with tarfile.open(output / "harbor-jobs.tar.gz", "w:gz") as archive:
            for name in ("logs", "results"):
                if (output / name).exists():
                    archive.add(output / name, arcname=name)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: task.py JOB_YAML", file=sys.stderr)
        return 2
    return run(Path(args[0]).resolve(), Path(os.environ["EVAL_OUTPUT_DIR"]))


if __name__ == "__main__":
    raise SystemExit(main())
