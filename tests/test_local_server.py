import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import yaml

from runners.unityhpc.jobs.local_server import attest_endpoint, select_port, wait_ready


@contextmanager
def endpoint(model, key, *, enforce_auth=True):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if enforce_auth and self.headers.get("Authorization") != "Bearer " + key:
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "specgym", "root": model}]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_foreign_healthy_server_cannot_mask_dead_own_server():
    with endpoint("/models/27b", "foreign-key") as url:
        own = SimpleNamespace(poll=lambda: 1, returncode=1)
        with pytest.raises(RuntimeError, match="Own model server exited"):
            wait_ready(own, url, "own-key", "/models/9b", timeout=1)


@pytest.mark.parametrize(
    "actual,key,enforce_auth,error",
    [
        ("/models/27b", "own-key", True, "different model"),
        ("/models/9b", "foreign-key", True, "belongs to another"),
        ("/models/9b", "own-key", False, "does not enforce authentication"),
    ],
)
def test_readiness_rejects_wrong_model_or_unowned_endpoint(actual, key, enforce_auth, error):
    with endpoint(actual, key, enforce_auth=enforce_auth) as url:
        own = SimpleNamespace(poll=lambda: None, returncode=None)
        with pytest.raises(RuntimeError, match=error):
            wait_ready(own, url, "own-key", "/models/9b", timeout=1)


def test_server_dying_during_attestation_is_not_ready():
    with endpoint("/models/9b", "own-key") as url:
        states = iter([None, 1])
        own = SimpleNamespace(poll=lambda: next(states), returncode=1)
        with pytest.raises(RuntimeError, match="during readiness"):
            wait_ready(own, url, "own-key", "/models/9b", timeout=1)


def test_concurrent_supervisors_keep_same_alias_models_and_credentials_separate(tmp_path):
    fake = tmp_path / "fake_vllm.py"
    fake.write_text(
        "import json, os, sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  if self.headers.get('Authorization') != 'Bearer '+os.environ['VLLM_API_KEY']:\n"
        "   self.send_response(401); self.end_headers(); return\n"
        "  self.send_response(200); self.end_headers()\n"
        "  self.wfile.write(json.dumps({'data':[{'id':'specgym','root':sys.argv[2]}]}).encode())\n"
        " def log_message(self,*args): pass\n"
        "HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()\n"
    )
    processes, credentials = [], []
    try:
        for model in ("/models/27b", "/models/9b"):
            output = tmp_path / Path(model).name
            (output / "results").mkdir(parents=True)
            port = select_port()
            script = output / "server.sh"
            script.write_text("exec " + shlex.join([sys.executable, str(fake), str(port), model]))
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "runners.unityhpc.jobs.local_server",
                    str(script),
                    str(port),
                    model,
                    str(output),
                ],
                env={**os.environ, "SLURM_JOB_ID": str(port)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            processes.append(process)
            endpoint_file = output / "model-endpoint.env"
            deadline = time.monotonic() + 10
            while (
                not endpoint_file.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert endpoint_file.exists()
            assert endpoint_file.stat().st_mode & 0o777 == 0o600
            values = dict(
                shlex.split(line)[1].split("=", 1)
                for line in endpoint_file.read_text().splitlines()
            )
            credentials.append(values)
            receipt = json.loads((output / "results/model-endpoint.json").read_text())
            assert receipt["requested_model"] == model and receipt["authenticated"]
            assert values["OPENAI_API_KEY"] not in json.dumps(receipt)
        first, second = credentials
        assert first["OPENAI_BASE_URL"] != second["OPENAI_BASE_URL"]
        assert first["OPENAI_API_KEY"] != second["OPENAI_API_KEY"]
        for values, model in zip(credentials, ("/models/27b", "/models/9b"), strict=True):
            assert attest_endpoint(values["OPENAI_BASE_URL"], values["OPENAI_API_KEY"], model)[
                "authenticated"
            ]
        with pytest.raises(Exception) as error:
            attest_endpoint(first["OPENAI_BASE_URL"], second["OPENAI_API_KEY"], "/models/9b")
        assert getattr(error.value, "code", None) == 401
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
            process.stderr.close()
    for model in ("27b", "9b"):
        assert not (tmp_path / model / "model-endpoint.env").exists()


@pytest.mark.parametrize("server_dies", [False, True])
def test_allocation_entrypoint_attests_endpoint_and_aborts_on_server_loss(tmp_path, server_dies):
    experiment = tmp_path / "experiment"
    runtime = tmp_path / "runtime"
    output = tmp_path / "output"
    for directory in (experiment / "agent", experiment / "sandbox", runtime / ".venv/bin", output):
        directory.mkdir(parents=True)
    definition = experiment / "sandbox/sandbox.def"
    definition.write_text("Bootstrap: localimage\nFrom: unused-test-image\n")
    (runtime / "sandbox.sif").write_bytes(b"existing test image")
    (runtime / "sandbox.def.sha256").write_text(hashlib.sha256(definition.read_bytes()).hexdigest())
    (runtime / ".venv/bin/harbor").write_text("#!/bin/sh\nexit 0\n")
    (runtime / ".venv/bin/harbor").chmod(0o755)
    marker = tmp_path / "harbor-started"
    finished = tmp_path / "harbor-finished"
    wrapper = runtime / ".venv/bin/python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "from urllib.request import Request,urlopen\n"
        "if sys.argv[1:3] == ['-m','agentic_eval.launch']:\n"
        " headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']}\n"
        " req=Request(os.environ['OPENAI_BASE_URL']+'/models',headers=headers)\n"
        " with urlopen(req) as response:\n"
        "  assert json.load(response)['data'][0]['root']=='/models/9b'\n"
        f" Path({str(marker)!r}).touch()\n"
        f" time.sleep({30 if server_dies else 0})\n"
        f" Path({str(finished)!r}).touch()\n"
        "else:\n"
        f" os.execv({sys.executable!r},[{sys.executable!r},*sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    fake = tmp_path / "fake.py"
    fake.write_text(
        "import json,os,sys,time,threading\nfrom pathlib import Path\n"
        "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  if self.headers.get('Authorization')!='Bearer '+os.environ['VLLM_API_KEY']:\n"
        "   self.send_response(401);self.end_headers();return\n"
        "  self.send_response(200);self.end_headers()\n"
        "  self.wfile.write(json.dumps({'data':[{'id':'specgym','root':sys.argv[2]}]}).encode())\n"
        " def log_message(self,*args):pass\n"
        "def fail_when_agent_starts():\n"
        f" while not Path({str(marker)!r}).exists():time.sleep(.05)\n"
        " os._exit(2)\n"
        f"if {server_dies!r}:threading.Thread(target=fail_when_agent_starts,daemon=True).start()\n"
        "HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()\n"
    )
    (experiment / "job.yaml").write_text("{}\n")
    (experiment / "agent/agent-config.yaml").write_text("model: /models/9b\n")
    (experiment / "runner.yaml").write_text(
        yaml.safe_dump(
            {
                "model_server": {
                    "port": 8000,
                    "context_length": 32,
                    "command": shlex.join(
                        [sys.executable, str(fake), "{port}", "{model}", "{context_length}"]
                    ),
                }
            }
        )
    )
    script = Path(__file__).resolve().parents[1] / "src/runners/unityhpc/jobs/task.sh"
    result = subprocess.run(
        ["bash", str(script), str(experiment / "job.yaml")],
        env={**os.environ, "HPC_OUTPUT_DIR": str(output), "EVAL_RUNTIME_ROOT": str(runtime)},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == int(server_dies), result.stderr
    assert marker.exists()
    assert finished.exists() != server_dies
    assert not (output / "model-endpoint.env").exists()
    receipt = json.loads((output / "results/model-endpoint.json").read_text())
    assert receipt["ready"] and receipt["authenticated"]
    assert bool(receipt.get("error")) == server_dies
    assert (output / "harbor-jobs.tar.gz").is_file()
