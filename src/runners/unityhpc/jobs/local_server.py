"""Own and authenticate one allocation's local vLLM endpoint before launching Harbor."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def select_port() -> int:
    # The socket cannot stay bound while vLLM binds it. Authentication and model
    # attestation make a race fail closed instead of accepting another job.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def attest_endpoint(url: str, api_key: str, model: str) -> dict:
    request = Request(url + "/models", headers={"Authorization": "Bearer " + api_key})
    with urlopen(request, timeout=5) as response:
        data = json.load(response)
    cards = data.get("data", [])
    if not cards or any(card.get("root") != model for card in cards):
        raise RuntimeError("Local endpoint serves a different model checkpoint")
    # Confirm the server enforces the per-allocation key, rather than merely
    # accepting our Authorization header on an unrelated unauthenticated server.
    try:
        with urlopen(url + "/models", timeout=5):
            raise RuntimeError("Local endpoint does not enforce authentication")
    except HTTPError as error:
        if error.code != 401:
            raise
    return {"models": cards, "authenticated": True}


def wait_ready(process, url: str, api_key: str, model: str, timeout: float = 1800) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Own model server exited with status {process.returncode}")
        try:
            receipt = attest_endpoint(url, api_key, model)
        except HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("Selected port belongs to another model server") from error
            if error.code not in (404, 503):
                raise
        except (URLError, TimeoutError, ConnectionError):
            pass
        else:
            if process.poll() is not None:
                raise RuntimeError("Own model server exited during readiness check")
            return receipt
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError("Own authenticated model server did not become ready")


def serve(script: Path, port: int, model: str, output: Path) -> None:
    endpoint_file = output / "model-endpoint.env"
    endpoint_file.unlink(missing_ok=True)
    api_key = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/v1"
    process = subprocess.Popen(
        ["bash", str(script)],
        env={**os.environ, "VLLM_API_KEY": api_key},
        start_new_session=True,
    )

    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    receipt = {
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "server_pid": process.pid,
        "port": port,
        "url": url,
        "requested_model": model,
        "ready": False,
    }
    receipt_path = output / "results/model-endpoint.json"
    try:
        receipt.update(wait_ready(process, url, api_key, model))
        receipt.update(ready=True, ready_at=datetime.now(UTC).isoformat())
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        pending = endpoint_file.with_suffix(".pending")
        pending.write_text(
            "export OPENAI_BASE_URL="
            + shlex.quote(url)
            + "\n"
            + "export OPENAI_API_KEY="
            + shlex.quote(api_key)
            + "\n"
        )
        pending.chmod(0o600)
        pending.replace(endpoint_file)
        process.wait()
        raise RuntimeError(f"Model server exited unexpectedly with status {process.returncode}")
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        endpoint_file.unlink(missing_ok=True)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        receipt.update(stopped_at=datetime.now(UTC).isoformat(), exit_code=process.returncode)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    serve(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
