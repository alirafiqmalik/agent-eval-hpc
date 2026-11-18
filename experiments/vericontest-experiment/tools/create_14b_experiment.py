"""Copy the validated pilot into an independent Qwen 14B experiment."""

import shutil
from pathlib import Path

import yaml

source = Path(__file__).resolve().parents[1]
target = source.parent / "vericontest-qwen14b-experiment"

if target.exists():
    raise FileExistsError(f"Already exists: {target}")

# Copy task data and code, excluding previous results and launch variants.
shutil.copytree(
    source,
    target,
    ignore=shutil.ignore_patterns(
        "output",
        "__pycache__",
        "*.pyc",
        "*.sif",
        "run.sh",
        "runner-l40s.yaml",
        "test.py",
        "prepare_14b.py",
        "create_14b_experiment.py",
    ),
)

shutil.copy2(
    source / "agent/agent-config-14b.yaml",
    target / "agent/agent-config.yaml",
)
shutil.copy2(
    source / "runner-a100-14b.yaml",
    target / "runner.yaml",
)

# Update references to task directories, sandbox images, and adapter imports.
for path in target.rglob("*"):
    if path.is_file() and path.suffix in {".yaml", ".toml", ".py", ".md", ".sh"}:
        text = path.read_text()
        text = text.replace(source.name, target.name)
        path.write_text(text)

for filename in ("job.yaml", "smoke.yaml", "oracle.yaml"):
    path = target / filename
    config = yaml.safe_load(path.read_text())
    config["job_name"] = "qwen14b-" + config["job_name"]
    path.write_text(yaml.safe_dump(config, sort_keys=False))

(target / "agent/agent-config-14b.yaml").unlink()
(target / "runner-a100-14b.yaml").unlink()

print("Created:", target)
print("Original 7B experiment is unchanged.")
