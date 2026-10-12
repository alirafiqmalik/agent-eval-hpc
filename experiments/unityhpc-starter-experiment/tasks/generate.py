"""Write one Harbor task folder for every HumanEval problem."""

from __future__ import annotations

import base64
import gzip
import json
import shutil
import urllib.request
from pathlib import Path

DATASET_REVISION = "6d43fb980f9fee3c892a914eda09951f772ad10d"
DATASET_URL = (
    f"https://raw.githubusercontent.com/openai/human-eval/{DATASET_REVISION}/data/HumanEval.jsonl.gz"
)
IMAGE = "experiments/unityhpc-starter-experiment/sandbox/sandbox.sif"
TASK_TOML = f"""version = "1.0"

[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = 600.0

[environment]
docker_image = "{IMAGE}"
cpus = 2
memory_mb = 4096
build_timeout_sec = 600.0
"""


def _load_problems() -> list[dict]:
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:
        payload = gzip.decompress(response.read())
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def _write_task(benchmark: Path, verifier: Path, problem: dict) -> None:
    name = problem["task_id"].replace("/", "_")
    task = benchmark / name
    if task.exists():
        shutil.rmtree(task)
    (task / "environment").mkdir(parents=True)
    (task / "environment/.gitkeep").write_text("")
    (task / "tests").mkdir()
    (task / "solution").mkdir()
    prompt = problem["prompt"]
    instruction = "Write the completed module to /app/solution.py.\n\n" + prompt
    (task / "instruction.md").write_text(instruction)
    (task / "task.toml").write_text(TASK_TOML)
    (task / "tests/hidden_test.txt").write_text(problem["test"])
    (task / "tests/entry_point.txt").write_text(problem["entry_point"] + "\n")
    shutil.copy(verifier / "test.sh", task / "tests/test.sh")
    shutil.copy(verifier / "grade.py", task / "tests/grade.py")
    reference = prompt + problem["canonical_solution"]
    encoded = base64.b64encode(reference.encode()).decode()
    script = (
        "#!/bin/bash\n"
        "python -c 'import base64, pathlib; "
        f'pathlib.Path("/app/solution.py").write_bytes(base64.b64decode("{encoded}"))\'\n'
    )
    (task / "solution/solve.sh").write_text(script)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    benchmark = root / "tasks/humaneval"
    verifier = root / "tools/verifier"
    problems = _load_problems()
    for problem in problems:
        _write_task(benchmark, verifier, problem)
    print(f"wrote {len(problems)} tasks to {benchmark}")


if __name__ == "__main__":
    main()
