"""Pair an ARM inference server with an x86 Harbor worker in one campaign."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import secrets
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def wait_endpoint(path: Path, timeout: int = 7200) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            endpoint = json.loads(path.read_text())
            try:
                req = Request(
                    endpoint["url"] + "/models",
                    headers={"Authorization": "Bearer " + endpoint["api_key"]},
                )
                with urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        return endpoint
            except (URLError, TimeoutError):
                pass
        time.sleep(5)
    raise TimeoutError("Paired model server did not become ready within two hours")


def serve(output: Path) -> None:
    runtime = json.loads(os.environ["EVAL_RUNNER_CONFIG"])
    server = runtime["model_server"]
    agent = json.loads(os.environ["EVAL_AGENT_CONFIG"])
    environment = dict(os.environ)
    root = Path(environment["EVAL_RUNTIME_ROOT"])
    environment["PATH"] = str(root / ".venv/bin") + ":" + environment["PATH"]
    cuda = root / ".venv/lib/python3.12/site-packages/nvidia/cu13"
    if (cuda / "bin/nvcc").is_file():
        environment["CUDA_HOME"] = str(cuda)
        environment["PATH"] = str(cuda / "bin") + ":" + environment["PATH"]
        environment["LD_LIBRARY_PATH"] = str(cuda / "lib") + (
            ":" + environment["LD_LIBRARY_PATH"] if environment.get("LD_LIBRARY_PATH") else ""
        )
    environment["HF_HOME"] = str(root / "hf")
    # A fresh key per allocation prevents other cluster users using this server.
    api_key = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("", 0))
        port = sock.getsockname()[1]
    command = shlex.split(
        server["command"].format(
            model=agent["model"], port=port, context_length=server["context_length"]
        )
    )
    command.extend(["--host", "0.0.0.0", "--api-key", api_key])
    endpoint = {"url": f"http://{socket.gethostname()}:{port}/v1", "api_key": api_key}
    record = {
        "architecture": platform.machine(),
        "model": agent["model"],
        "packages": {p: importlib.metadata.version(p) for p in ("vllm", "torch", "transformers")},
        "server_host": socket.gethostname(),
        "port": port,
    }
    (output / "server-runtime.json").write_text(json.dumps(record, indent=2) + "\n")
    ready = output / "endpoint.json"
    ready.unlink(missing_ok=True)
    with (output / "model-server.log").open("w") as stream:
        process = subprocess.Popen(
            command, env=environment, stdout=stream, stderr=subprocess.STDOUT
        )
        try:
            pending = output / "endpoint.pending"
            pending.write_text(json.dumps(endpoint))
            pending.chmod(0o600)
            pending.replace(ready)
            while not (output.parent / "evaluation/worker-exit.json").is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"Model server exited with status {process.returncode}")
                time.sleep(5)
        finally:
            ready.unlink(missing_ok=True)
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    output = Path(os.environ["HPC_OUTPUT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "serve":
        serve(output)
    elif sys.argv[1] == "wait":
        endpoint = wait_endpoint(output.parent / "model-server/endpoint.json")
        # The caller sources this private file; credentials never enter result logs.
        target = output / "model-endpoint.env"
        target.write_text(
            "export OPENAI_BASE_URL="
            + shlex.quote(endpoint["url"])
            + "\n"
            + "export OPENAI_API_KEY="
            + shlex.quote(endpoint["api_key"])
            + "\n"
        )
        target.chmod(0o600)
    else:
        raise ValueError("Expected serve or wait")
