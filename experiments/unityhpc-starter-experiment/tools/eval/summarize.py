"""Turn Harbor reward files into one score summary."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def summarize(run_dir: Path) -> int:
    logs = run_dir / "logs"
    problems = []
    if logs.is_dir():
        trial_dirs = {
            path.parent.parent if path.parent.name == "verifier" else path.parent
            for path in logs.rglob("reward.txt")
        }
        for result_file in logs.rglob("result.json"):
            if "task_name" in json.loads(result_file.read_text()):
                trial_dirs.add(result_file.parent)
        for trial_dir in sorted(trial_dirs):
            reward_file = trial_dir / "verifier/reward.txt"
            try:
                reward = float(reward_file.read_text().strip())
            except (FileNotFoundError, ValueError):
                reward = None
            trial = trial_dir.relative_to(logs).as_posix()
            result_file = trial_dir / "result.json"
            result = json.loads(result_file.read_text()) if result_file.is_file() else {}
            exception = result.get("exception_info") or {}
            problems.append(
                {
                    "trial": trial,
                    "reward": reward,
                    "exception_type": exception.get("exception_type"),
                }
            )
    passed = sum(1 for item in problems if item["reward"] is not None and item["reward"] >= 1)
    errors = sum(1 for item in problems if item["exception_type"])
    job_results = [json.loads(path.read_text()) for path in logs.glob("*/result.json")]
    expected_total = (
        sum(result["n_total_trials"] for result in job_results) if job_results else None
    )
    summary = {
        "passed": passed,
        "total": len(problems),
        "expected_total": expected_total,
        "complete": expected_total == len(problems)
        and all(result.get("finished_at") for result in job_results),
        "errors": errors,
        "problems": problems,
    }
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: summarize.py RUN_DIR", file=sys.stderr)
        return 2
    return summarize(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
