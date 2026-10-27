"""Keep staged checkpoints alive while the owning experiment drivers are alive."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml


def process_identity(pid: int) -> str | None:
    try:
        # Linux start time prevents a recycled PID from retaining the workspace.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def renew(config: dict) -> bool:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-i",
        str(Path(config["identity_file"]).expanduser()),
        config["ssh_host"],
        shlex.join(["ws_extend", config["name"], str(config["duration_days"])]),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"{datetime.now(UTC).isoformat()} workspace renewal failed: {error}", flush=True)
        return False
    print(
        f"{datetime.now(UTC).isoformat()} workspace renewal exit={result.returncode}\n"
        + result.stdout
        + result.stderr,
        flush=True,
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, action="append", default=[])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(args.connection.read_text())["workspace_lease"]
    if args.once:
        raise SystemExit(0 if renew(config) else 1)
    if not args.parent_pid:
        parser.error("--parent-pid is required unless --once is set")
    owners = {pid: process_identity(pid) for pid in args.parent_pid}
    next_refresh = 0.0
    while any(identity and process_identity(pid) == identity for pid, identity in owners.items()):
        if time.monotonic() >= next_refresh:
            succeeded = renew(config)
            next_refresh = time.monotonic() + (7 * 86400 if succeeded else 3600)
        time.sleep(30)


if __name__ == "__main__":
    main()
