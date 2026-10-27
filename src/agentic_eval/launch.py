"""Start one Harbor job and collect its logs into the experiment output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

from agentic_eval.jobs import repository_root, validate_job


def _agent(experiment: Path) -> dict:
    config = (
        json.loads(os.environ["EVAL_AGENT_CONFIG"])
        if os.environ.get("EVAL_AGENT_CONFIG")
        else yaml.safe_load((experiment / "agent/agent-config.yaml").read_text())
    )
    if type(config) is not dict or not isinstance(config.get("model"), str) or not config["model"]:
        raise ValueError("agent-config.yaml requires a model id")
    return config


def harbor_command(
    job: Path, jobs_dir: Path, base_url: str | None, *, experiment: Path | None = None
) -> list[str]:
    """Build the Harbor invocation for this experiment."""
    harbor = shutil.which("harbor")
    if harbor is None:
        raise ValueError("harbor is not installed")
    experiment = experiment or job.resolve().parent
    agent = _agent(experiment)
    prompt = experiment / "agent/prompts/system.md"
    if not prompt.is_file():
        raise ValueError(f"missing system prompt: {prompt}")
    job_config = yaml.safe_load(job.read_text())
    environment = job_config.get("environment", {}).get("import_path") or "singularity"
    command = [
        harbor,
        "run",
        "--config",
        str(job),
        "--env",
        environment,
        "--mounts",
        json.dumps(
            [
                {
                    "type": "bind",
                    "source": "/etc/resolv.conf",
                    "target": "/etc/resolv.conf",
                    "read_only": True,
                }
            ]
        ),
        "--jobs-dir",
        str(jobs_dir),
        "--extra-instruction-path",
        str(prompt),
        "--quiet",
        "--yes",
    ]
    if version := agent.get("version"):
        command.extend(["--ak", f"version={version}"])
    if max_output_tokens := agent.get("max_output_tokens"):
        command.extend(["--ae", f"QWEN_CODE_MAX_OUTPUT_TOKENS={max_output_tokens}"])
    if base_url and all(a.get("name") == "qwen-coder" for a in job_config.get("agents", [])):
        # Harbor reads the allocation's private OPENAI_API_KEY from its
        # environment. An explicit key here would override that credential.
        command.extend(["--ak", f"base_url={base_url}"])
    return command


def post_run(experiment: Path, run_dir: Path) -> None:
    """Write the combined trajectory and the score summary for one run."""
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    scripts = (
        experiment / "tools/trajectory/export.py",
        experiment / "tools/eval/summarize.py",
    )
    export, summarize = scripts
    if subprocess.call([sys.executable, str(export), str(logs)]) != 0:
        raise ValueError(f"trajectory export failed: {export}")
    if subprocess.call([sys.executable, str(summarize), str(run_dir)]) != 0:
        raise ValueError(f"result summary failed: {summarize}")


def stage_job(job: Path, staging_dir: Path, image: Path) -> Path:
    """Copy Harbor tasks into writable output and point them at the built image."""
    root = repository_root(job.resolve())
    config = yaml.safe_load(job.read_text())
    if override := os.environ.get("EVAL_JOB_CONFIG"):
        # Campaigns can shard a normal Harbor config without modifying bundled source.
        from harbor.models.job.config import JobConfig

        config = json.loads(override)
        JobConfig.model_validate(config)
    for agent in config.get("agents", []):
        if agent.get("model_name", "").startswith("hosted_vllm/"):
            # LiteLLM's hosted_vllm provider uses its own credential name.
            # Harbor resolves this reference at execution and scrubs the secret
            # from artifacts; never serialize the ephemeral key into job YAML.
            agent.setdefault("env", {}).setdefault("HOSTED_VLLM_API_KEY", "${OPENAI_API_KEY}")
    staging_dir.mkdir(parents=True, exist_ok=True)
    for section in ("datasets", "tasks"):
        for index, item in enumerate(config.get(section, [])):
            if not isinstance(item, dict) or not item.get("path"):
                continue
            source = Path(item["path"])
            if not source.is_absolute():
                source = root / source
            destination = staging_dir / f"{section}-{index}"
            if destination.exists():
                shutil.rmtree(destination)
            if source.is_dir():
                selected = item.get("task_names") if section == "datasets" else None
                if selected:
                    destination.mkdir(parents=True)
                    for name in selected:
                        if Path(name).name != name or name in (".", ".."):
                            raise ValueError(f"invalid task name: {name}")
                        shutil.copytree(source / name, destination / name)
                else:
                    shutil.copytree(source, destination)
                task_files = destination.rglob("task.toml")
            else:
                raise ValueError(f"task path is not a directory: {source}")
            for task_file in task_files:
                original = task_file.read_text()
                patched, count = re.subn(
                    r'(?m)^docker_image\s*=\s*"[^"]+\.sif"[ \t]*$',
                    f'docker_image = "{image.resolve()}"',
                    original,
                )
                if count != 1:
                    raise ValueError(f"expected one sandbox image in {task_file}")
                task_file.chmod(task_file.stat().st_mode | stat.S_IWUSR)
                task_file.write_text(patched)
            item["path"] = str(destination.resolve())
    staged_job = staging_dir / "job.yaml"
    staged_job.write_text(yaml.safe_dump(config, sort_keys=False))
    return staged_job


def run_job(
    job: Path, jobs_dir: Path, base_url: str | None = None, image: Path | None = None
) -> int:
    """Validate the job, run Harbor, then write logs and results."""
    root = repository_root(job.resolve())
    validate_job(job)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    source_job = job
    if image is not None:
        job = stage_job(job, jobs_dir.parent / "staged", image)
    status = subprocess.call(
        harbor_command(job, jobs_dir, base_url, experiment=source_job.resolve().parent), cwd=root
    )
    post_run(source_job.resolve().parent, jobs_dir.parent)
    return status


def main(argv: list[str] | None = None) -> int:
    """Entry point for the cluster script: `python -m agentic_eval.launch`."""
    parser = argparse.ArgumentParser(description="Run one Harbor job into a logs directory")
    parser.add_argument("job", type=Path)
    parser.add_argument("--jobs-dir", type=Path, required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--image", type=Path)
    args = parser.parse_args(argv)
    return run_job(args.job, args.jobs_dir, args.base_url, args.image)


def unpack_run(destination: Path) -> None:
    """Extract a fetched Harbor archive into the run directory."""
    for archive in destination.rglob("harbor-jobs.tar.gz"):
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")


if __name__ == "__main__":
    raise SystemExit(main())
