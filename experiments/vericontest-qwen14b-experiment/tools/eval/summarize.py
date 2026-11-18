"""Summarize scored trials and retain infrastructure errors separately."""

import csv
import json
import sys
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def summarize(run_dir):
    logs = run_dir / "logs"
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)

    rows = []
    job_results = []

    for path in sorted(logs.rglob("result.json")):
        result = read_json(path)

        if "task_name" not in result:
            if "n_total_trials" in result:
                job_results.append(result)
            continue

        trial = path.parent
        verifier = trial / "verifier"
        judgment = read_json(verifier / "judgment.json")
        generation = read_json(trial / "agent/generation.json")
        settings = generation.get("settings", {})
        usage = generation.get("usage") or {}
        exception = result.get("exception_info") or {}

        reward_path = verifier / "reward.txt"
        try:
            reward = float(reward_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            reward = None

        references_ok = all(
            read_json(verifier / f"reference-{label}.json").get("status") == "pass"
            for label in ("plain", "verus")
        )
        expected_reward = {
            "pass": 1.0,
            "fail": 0.0,
        }.get(judgment.get("status"))

        scored = (
            not exception
            and references_ok
            and expected_reward is not None
            and reward == expected_reward
        )

        rows.append(
            {
                "trial": trial.relative_to(logs).as_posix(),
                "task_name": result["task_name"],
                "problem": judgment.get("problem"),
                "task": judgment.get("task"),
                "model": settings.get("model"),
                "returned_model": generation.get("returned_model"),
                "scored": scored,
                "reward": reward if scored else None,
                "status": judgment.get("status") if scored else "infrastructure_error",
                "category": judgment.get("category") if scored else None,
                "exception_type": exception.get("exception_type"),
                "references_ok": references_ok,
                "tests_passed": judgment.get("tests_passed"),
                "test_count": judgment.get("test_count"),
                "failed_case": judgment.get("failed_case"),
                "generation_seconds": generation.get("elapsed_seconds"),
                "evaluation_seconds": judgment.get("evaluation_seconds"),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "finish_reason": generation.get("finish_reason"),
                "max_tokens": settings.get("max_tokens"),
                "temperature": settings.get("temperature"),
                "top_p": settings.get("top_p"),
                "seed": settings.get("seed"),
                "prompt_sha256": generation.get("prompt_sha256"),
                "response_sha256": generation.get("response_sha256"),
            }
        )

    expected_total = sum(item["n_total_trials"] for item in job_results) if job_results else None
    complete = (
        expected_total is not None
        and expected_total == len(rows)
        and all(item.get("finished_at") for item in job_results)
    )
    scored_count = sum(row["scored"] for row in rows)
    passed = sum(row["reward"] == 1.0 for row in rows)
    scored_complete = complete and scored_count == expected_total

    summary = {
        "passed": passed,
        "total": len(rows),
        "expected_total": expected_total,
        "complete": complete,
        "scored": scored_count,
        "scored_complete": scored_complete,
        "errors": len(rows) - scored_count,
        "pass_rate_pct": (
            100 * passed / expected_total if scored_complete and expected_total else None
        ),
        "problems": rows,
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    if rows:
        with (results / "attempts.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    print(
        f"Trials: {len(rows)}; scored: {scored_count}; "
        f"passed: {passed}; infrastructure errors: {len(rows) - scored_count}"
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: summarize.py RUN_DIR")
    raise SystemExit(summarize(Path(sys.argv[1])))
