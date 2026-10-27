"""Run the unchanged upstream evaluator; distinguish invalid specs from broken runs."""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path


def main() -> None:
    logs = Path("/logs/verifier")
    logs.mkdir(parents=True, exist_ok=True)
    # Structural feedback alone does not detect a missing compiler installation.
    # Compile and prove a task-independent assertion before evaluating a model.
    probe = Path("/tmp/specgym_toolchain_probe.rs")
    probe.write_text("use vstd::prelude::*; verus! { fn main() { assert(true); } }\n")
    check = subprocess.run(
        ["/verus-x86-linux/verus", "--compile", str(probe)],
        cwd=probe.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    execution = None
    if check.returncode == 0:
        execution = subprocess.run(
            [str(probe.with_suffix(""))], capture_output=True, text=True, timeout=10
        )
    return_code = check.returncode or (execution.returncode if execution else 1)
    (logs / "toolchain-check.json").write_text(
        json.dumps(
            {
                "return_code": return_code,
                "stdout": check.stdout,
                "stderr": check.stderr,
                "execution_stderr": execution.stderr if execution else None,
            },
            indent=2,
        )
        + "\n"
    )
    if return_code != 0:
        raise RuntimeError("Verus toolchain self-check failed; see toolchain-check.json")
    # Restore the trusted evaluator before hidden verification.
    runtime = Path("/opt/verus_gym_specgen_check_runtime")
    shutil.rmtree(runtime, ignore_errors=True)
    with tarfile.open(
        "/opt/verus_gym_specgen_check_bundle/verus_gym_specgen_check_runtime.tar.gz"
    ) as bundle:
        bundle.extractall("/opt", filter="data")
    hidden = Path("/home/task_testing_specific_artifacts")
    shutil.rmtree(hidden, ignore_errors=True)
    hidden.mkdir()
    with tarfile.open("/tests/task_testing_specific_artifacts.tar.gz") as bundle:
        bundle.extractall(hidden, filter="data")
    submitted = Path("/home/dev/solve.rs")
    if not submitted.is_file():
        raise RuntimeError("submission is missing")
    shutil.copyfile(submitted, logs / "solve.rs")
    output = logs / "compressed_verdicts"
    shutil.rmtree(output, ignore_errors=True)
    details = Path("/tmp/verifier_attempts")
    shutil.rmtree(details, ignore_errors=True)
    result = subprocess.run(
        [
            "/usr/local/bin/verus_gym_specgen_check",
            f"--path_to_solve_rs_submitted={submitted}",
            "--evaluate_sample_only=false",
            f"--PARENT_OF_DETAILED_SAVE_DIR={details}",
            f"--PARENT_OF_COMPRESSED_SAVE_DIR={output}",
            f"--PATH_TO_DATA_FROM_CONSTRUCTION={hidden}",
            "--run_id=run_on_all_samples",
        ],
        check=False,
    )
    archive = output / "run_on_all_samples/results.zip"
    if not archive.is_file():
        # No reward file: Harbor must record an infrastructure error, not a model failure.
        raise RuntimeError(f"evaluator exited {result.returncode} without feedback")
    with zipfile.ZipFile(archive) as bundle:
        feedback = json.loads(bundle.read("EvaluationFeedback.json"))
    syntax = feedback.get("syntax_check_feedback") or {}
    execution = feedback.get("execution_feedback")
    if syntax.get("has_passed_syntax_checks") and execution is None:
        raise RuntimeError("syntax passed but evaluator produced no testcase results")
    passed = bool(syntax.get("has_passed_syntax_checks")) and all(
        execution.get(bucket)
        and all(item["does_testcase_have_expected_verdict"] for item in execution[bucket].values())
        for bucket in ("pre_sound", "pre_complete", "post_complete", "post_sound")
    )
    (logs / "reward.txt").write_text("1\n" if passed else "0\n")


if __name__ == "__main__":
    main()
