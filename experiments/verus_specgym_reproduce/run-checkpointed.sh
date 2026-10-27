#!/usr/bin/env bash
# Start or resume a persistent, smoke-gated campaign; outputs are collected automatically.
# Usage: ./experiments/verus_specgym_reproduce/run-checkpointed.sh RUN_ID
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${repo_root}"
uv run --locked --group plots python - "$repo_root" "$@" <<'PY'
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
parser = argparse.ArgumentParser()
parser.add_argument("run_id")
parser.add_argument("--controls-output", type=Path)
parser.add_argument("--controls-run-id")
parser.add_argument("--profile", action="append", help="Run only these checkpointed.yaml profiles")
parser.add_argument("--source-from", type=Path, help="Reuse an earlier batch's frozen source and matrix")
parser.add_argument("--inference-timeout-seconds", type=int, help="HTTP timeout; new cohorts default to 7200, without extending task budgets")
args = parser.parse_args(sys.argv[2:])
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,24}", args.run_id):
    parser.error("Use a lowercase run ID of at most 25 letters, digits, or hyphens")
experiment = root / "experiments/verus_specgym_reproduce"
output = experiment / "output" / args.run_id
output.mkdir(parents=True, exist_ok=True)
settings_path = output / "launcher.json"
saved = json.loads(settings_path.read_text()) if settings_path.is_file() else {}
controls = (args.controls_output or Path(saved.get("controls_output", output / "preflight"))).resolve()
control_id = args.controls_run_id or saved.get("controls_run_id", args.run_id + "-ctrl")
settings = {"controls_output": str(controls), "controls_run_id": control_id}
inference_timeout = args.inference_timeout_seconds
if inference_timeout is None:
    # Resuming an old cohort must preserve its original request identity.
    inference_timeout = saved.get("inference_timeout_seconds") if saved else 7200
if inference_timeout is not None:
    if inference_timeout <= 0:
        parser.error("Inference request timeout must be positive")
    settings["inference_timeout_seconds"] = inference_timeout
profiles = sorted(set(args.profile or saved.get("profiles", [])))
if profiles:
    settings["profiles"] = profiles
source_from = args.source_from or saved.get("source_from")
if source_from:
    source_from = Path(source_from).resolve()
    settings["source_from"] = str(source_from)
if saved and settings != saved:
    raise SystemExit("Saved launch settings differ; use a new run ID")
if source_from:
    source_manifest = json.loads((source_from / "source-snapshot.json").read_text())
    snapshot = Path(source_manifest["snapshot"])
    if not (snapshot / "experiments/verus_specgym_reproduce/checkpointed.yaml").is_file():
        raise SystemExit("Frozen source matrix is missing")
    target = output / "source-snapshot.json"
    if target.exists() and json.loads(target.read_text()) != source_manifest:
        raise SystemExit("Existing frozen source differs; use a new run ID")
    with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(source_manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
unit = "specgym-" + args.run_id + ".service"
unit_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
unit_dir.mkdir(parents=True, exist_ok=True)
uv = shutil.which("uv")
if not uv:
    raise SystemExit("uv is required; run scripts/setup.sh first")

def command(parts):
    # These are systemd arguments, not shell commands. Escape specifier expansion.
    return " ".join(json.dumps(str(p).replace("%", "%%")) for p in parts)

prefix = [uv, "run", "--locked", "--group", "plots", "python"]
prepare = prefix + [experiment / "tools/audit.py", "--run-id", control_id,
                    "--output", controls, "--controls-only"]
run = prefix + [experiment / "tools/batch.py", "--run-id", args.run_id,
                "--matrix", experiment / "checkpointed.yaml",
                "--controls-from", controls / "controls"]
for profile in profiles:
    run.extend(["--profile", profile])
if inference_timeout is not None:
    run.extend(["--inference-timeout-seconds", str(inference_timeout)])
content = "\n".join([
    "[Unit]", "Description=Checkpointed Verus SpecGym reproduction",
    "StartLimitIntervalSec=3600", "StartLimitBurst=3", "[Service]", "Type=exec",
    "WorkingDirectory=" + str(root).replace("%", "%%"),
    "Environment=PYTHONUNBUFFERED=1 UNITYHPC_CONTROLLER_TIMEOUT=180",
    "ExecStartPre=" + command(prepare), "ExecStart=" + command(run),
    "TimeoutStartSec=infinity", "TimeoutStopSec=30", "Restart=on-failure", "RestartSec=300",
    "RestartPreventExitStatus=78",
    "StandardOutput=append:" + str(output / "driver.log"), "StandardError=inherit",
    "[Install]", "WantedBy=default.target", "",
])
path = unit_dir / unit
if path.exists() and path.read_text() not in (
    content, content.replace("RestartPreventExitStatus=78\n", "")
):
    raise SystemExit("Existing unit settings differ; use a new run ID")
path.write_text(content)
subprocess.run(["systemd-analyze", "--user", "verify", str(path)], check=True)
with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as stream:
    temporary = Path(stream.name)
    try:
        stream.write(json.dumps(settings, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary.replace(settings_path)
    finally:
        temporary.unlink(missing_ok=True)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(["systemctl", "--user", "enable", "--now", "--no-block", unit], check=True)
print(f"Started/resumed {unit}; logs and results: {output}")
PY
