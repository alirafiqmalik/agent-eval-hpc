"""Freeze source and run paper models plus early A100/H100 baselines independently."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agentic_eval.unity import request_for
from runners.discovery import load_runner

ROOT = Path(__file__).resolve().parents[3]
RELATIVE = Path("experiments/verus_specgym_reproduce")
EXPERIMENT = ROOT / RELATIVE


def freeze(output: Path) -> Path:
    saved = output / "source-snapshot.json"
    if saved.is_file():
        destination = Path(json.loads(saved.read_text())["snapshot"])
        if not (destination / RELATIVE / "tools/campaign.py").is_file():
            raise RuntimeError("Saved source snapshot is missing")
        return destination
    connection = ROOT / "src/runners/unityhpc/connections/verus-specgym.yaml"
    runner = load_runner(ROOT, "unityhpc", connection)
    manifest = runner.plan(
        request_for(ROOT, EXPERIMENT / "job.yaml", "specgym-source-inventory", connection)
    )["bundle"]
    destination = ROOT / ".cache/specgym-sources" / manifest["sha256"]
    for entry in manifest["files"]:
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(ROOT / entry["path"], target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"Source snapshot mismatch: {entry['path']}")
    link = destination / RELATIVE / "output"
    if not link.exists():
        link.symlink_to(EXPERIMENT / "output", target_is_directory=True)
    saved.write_text(
        json.dumps(
            {
                "source_sha256": manifest["sha256"],
                "snapshot": str(destination),
                "files": len(manifest["files"]),
                "original": str(ROOT),
            },
            indent=2,
        )
        + "\n"
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", default="specgym-all-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    )
    parser.add_argument(
        "--early-only", action="store_true", help="Keep an existing paper driver running"
    )
    parser.add_argument("--download-run-id", help="Reuse a completed small-model download campaign")
    parser.add_argument("--single-gpu", action="store_true", help="Run single-gpu.yaml profiles")
    parser.add_argument("--profile", action="append", help="Select a single-GPU profile label")
    parser.add_argument("--controls-from", type=Path)
    parser.add_argument("--inference-timeout-seconds", type=int)
    parser.add_argument(
        "--matrix", type=Path, help="Named profiles, shard concurrency and resume sources"
    )
    args = parser.parse_args()
    output = EXPERIMENT / "output" / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    source = freeze(output)
    # The remote scientific bundle remains frozen. Local orchestration can be
    # repaired independently without changing accepted controller identities.
    campaign = ROOT / RELATIVE / "tools/campaign.py"
    connection_dir = source / "src/runners/unityhpc/connections"
    jobs = {
        "qwen9b": ["--suite", "early", "--model", "qwen3.5-9b"],
        "qwen27b": [
            "--suite",
            "early",
            "--model",
            "qwen3.5-27b",
            "--runtime-config",
            str(source / RELATIVE / "runner-early-h100.yaml"),
            "--connection",
            str(connection_dir / "specgym-h100.yaml"),
        ],
    }
    if args.download_run_id:
        for argv in jobs.values():
            argv.extend(["--download-run-id", args.download_run_id])
    if args.single_gpu or args.matrix:
        matrix = args.matrix.resolve() if args.matrix else source / RELATIVE / "single-gpu.yaml"
        if args.matrix and matrix.is_relative_to(ROOT):
            # A resumed cohort must keep its original profiles and budgets even
            # when the workspace matrix has been repaired for a later cohort.
            matrix = source / matrix.relative_to(ROOT)
        profiles = yaml.safe_load(matrix.read_text())["profiles"]
        if args.profile and set(args.profile) - profiles.keys():
            parser.error("Unknown single-GPU profile")
        jobs = {}
        profile_connections = (
            ROOT / "src/runners/unityhpc/connections" if args.matrix else connection_dir
        )
        for label, profile in profiles.items():
            if args.profile and label not in args.profile:
                continue
            jobs[label] = [
                "--suite",
                profile.get("suite", "early"),
                "--model",
                profile["model"],
                "--runtime-config",
                str(source / RELATIVE / profile["runner"]),
                "--connection",
                str(profile_connections / profile["connection"]),
            ]
            download = profile.get("download_run_id", args.download_run_id)
            if download:
                jobs[label].extend(["--download-run-id", download])
            if model_cache := profile.get("model_cache"):
                jobs[label].extend(["--model-cache", model_cache])
            jobs[label].extend(["--parallel-shards", str(profile.get("parallel_shards", 1))])
            if profile.get("task_checkpoints"):
                jobs[label].append("--task-checkpoints")
            if args.controls_from:
                jobs[label].extend(["--controls-from", str(args.controls_from.resolve())])
            if fast := profile.get("fast_connection"):
                jobs[label].extend(["--fast-connection", str(connection_dir / fast)])
            if reuse := profile.get("reuse_completed"):
                previous = EXPERIMENT / "output" / reuse
                if (previous / "campaign.json").is_file():
                    jobs[label].extend(["--reuse-completed", str(previous)])
    elif not args.early_only:
        jobs["paper"] = []
    if args.inference_timeout_seconds is not None:
        if args.inference_timeout_seconds <= 0:
            parser.error("Inference request timeout must be positive")
        for argv in jobs.values():
            argv.extend(["--inference-timeout-seconds", str(args.inference_timeout_seconds)])
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1"}
    with (output / "orchestration.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "started_at": datetime.now(UTC).isoformat(),
                    "scientific_source": str(source),
                    "files": {
                        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in (
                            str(RELATIVE / "tools/batch.py"),
                            str(RELATIVE / "tools/campaign.py"),
                            "scripts/unity_eval.py",
                            "src/runners/unityhpc/adapter.py",
                            "src/runners/unityhpc/client.py",
                        )
                    },
                    "jobs": jobs,
                }
            )
            + "\n"
        )
    processes = {}
    statuses = {}
    for name, argv in jobs.items():
        log = output / f"{name}-driver.log"
        with log.open("a") as stream:
            processes[name] = subprocess.Popen(
                [
                    sys.executable,
                    str(campaign),
                    "run",
                    "--source-root",
                    str(source),
                    "--run-id",
                    args.run_id + "-" + name,
                    *argv,
                ],
                cwd=source,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        print(f"Started {name}: {log}", flush=True)
    combined = False
    while len(statuses) != len(processes):
        for name, process in processes.items():
            if name not in statuses and (code := process.poll()) is not None:
                statuses[name] = code
                print(f"{name} driver exited with status {code}", flush=True)
        if (
            not args.single_gpu
            and not args.matrix
            and not combined
            and statuses.get("qwen9b") == statuses.get("qwen27b") == 0
        ):
            states = [
                json.loads(
                    (
                        EXPERIMENT / "output" / (args.run_id + "-" + name) / "campaign.json"
                    ).read_text()
                )
                for name in ("qwen9b", "qwen27b")
            ]
            early = output / "early-combined"
            early.mkdir(exist_ok=True)
            (early / "campaign.json").write_text(
                json.dumps(
                    {
                        "suite": "early",
                        "models": [m for s in states for m in s["models"]],
                        "shards": [shard for s in states for shard in s["shards"]],
                        "execution": {s["models"][0]: s["execution"] for s in states},
                    },
                    indent=2,
                )
                + "\n"
            )
            subprocess.run(
                [sys.executable, str(source / RELATIVE / "tools/eval/plots.py"), str(early)],
                env=environment,
                check=True,
            )
            combined = True
        (output / "drivers.json").write_text(
            json.dumps(
                {
                    name: {
                        "pid": process.pid,
                        "exit_code": statuses.get(name),
                        "log": str(output / f"{name}-driver.log"),
                    }
                    for name, process in processes.items()
                },
                indent=2,
            )
            + "\n"
        )
        if len(statuses) != len(processes):
            time.sleep(30)
    # A terminal smoke decision requires a reviewed new attempt, not an endless
    # service restart that fetches and rejects the identical collected result.
    raise SystemExit(78 if 78 in statuses.values() else int(any(statuses.values())))


if __name__ == "__main__":
    main()
