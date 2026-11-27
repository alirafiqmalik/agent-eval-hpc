"""Write the Modal starter tasks: a CPU probe, a GPU probe, and two HumanEval problems.

The GPU probe requests the model server's GPU type from runner.yaml, so one
value selects the GPU for both. Run again after you change runner.yaml.
"""

from __future__ import annotations

import base64
import gzip
import json
import shutil
import urllib.request
from pathlib import Path

import yaml

DATASET_REVISION = "6d43fb980f9fee3c892a914eda09951f772ad10d"
DATASET_URL = (
    f"https://raw.githubusercontent.com/openai/human-eval/{DATASET_REVISION}/data/HumanEval.jsonl.gz"
)
# Two short problems keep the agent turns, and so the model tokens, small.
HUMANEVAL = ("HumanEval/2", "HumanEval/23")
CPU = {"cpus": 2, "memory_mb": 4096}
PROBE_INSTRUCTION = (
    "This task checks the sandbox hardware. The verifier runs the check. "
    "Change no files and reply with the word done.\n"
)


def _task_toml(cpus: int, memory_mb: int, gpu: str | None = None) -> str:
    gpu_lines = f'gpus = 1\ngpu_types = ["{gpu}"]\n' if gpu else ""
    return f"""version = "1.0"

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 600.0

[environment]
cpus = {cpus}
memory_mb = {memory_mb}
{gpu_lines}build_timeout_sec = 900.0
"""


def _new_task(benchmark: Path, name: str, root: Path, instruction: str, toml: str) -> Path:
    task = benchmark / name
    if task.exists():
        shutil.rmtree(task)
    for part in ("environment", "tests", "solution"):
        (task / part).mkdir(parents=True)
    shutil.copy(root / "sandbox/Dockerfile", task / "environment/Dockerfile")
    (task / "instruction.md").write_text(instruction)
    (task / "task.toml").write_text(toml)
    return task


def _probe(benchmark: Path, root: Path, name: str, probe: str, config: dict, toml: str) -> None:
    task = _new_task(benchmark, name, root, PROBE_INSTRUCTION, toml)
    verifier = root / "tools/verifier"
    shutil.copy(verifier / "probe.sh", task / "tests/test.sh")
    shutil.copy(verifier / probe, task / "tests/probe.py")
    (task / "tests/probe-config.json").write_text(json.dumps(config, indent=2) + "\n")
    (task / "solution/solve.sh").write_text("#!/bin/bash\n# The verifier runs the check.\ntrue\n")


def _humaneval(benchmark: Path, root: Path, problem: dict) -> None:
    prompt = problem["prompt"]
    instruction = "Write the completed module to /app/solution.py.\n\n" + prompt
    task = _new_task(
        benchmark, problem["task_id"].replace("/", "_"), root, instruction, _task_toml(**CPU)
    )
    verifier = root / "tools/verifier"
    (task / "tests/hidden_test.txt").write_text(problem["test"])
    (task / "tests/entry_point.txt").write_text(problem["entry_point"] + "\n")
    shutil.copy(verifier / "test.sh", task / "tests/test.sh")
    shutil.copy(verifier / "grade.py", task / "tests/grade.py")
    encoded = base64.b64encode((prompt + problem["canonical_solution"]).encode()).decode()
    (task / "solution/solve.sh").write_text(
        "#!/bin/bash\n"
        "python -c 'import base64, pathlib; "
        f'pathlib.Path("/app/solution.py").write_bytes(base64.b64decode("{encoded}"))\'\n'
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    benchmark = root / "tasks/starter"
    server = yaml.safe_load((root / "runner.yaml").read_text())["model_server"]
    _probe(benchmark, root, "cpu-probe", "cpu_probe.py", CPU, _task_toml(**CPU))
    gpu_config = {
        "names": server["gpu_names"],
        "memory_mb": server["gpu_memory_mb"],
        "tolerance_mb": server["gpu_memory_tolerance_mb"],
    }
    _probe(
        benchmark, root, "gpu-probe", "gpu_probe.py", gpu_config, _task_toml(2, 4096, server["gpu"])
    )
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:
        lines = gzip.decompress(response.read()).splitlines()
    problems = {p["task_id"]: p for p in (json.loads(line) for line in lines if line.strip())}
    for task_id in HUMANEVAL:
        _humaneval(benchmark, root, problems[task_id])
    for script in benchmark.rglob("*.sh"):
        script.chmod(0o755)
    print(f"wrote {2 + len(HUMANEVAL)} tasks to {benchmark}")


if __name__ == "__main__":
    main()
