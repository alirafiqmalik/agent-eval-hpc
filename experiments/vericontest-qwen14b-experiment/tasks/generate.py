"""Generate nine Harbor tasks for the three-problem CodeGen pilot."""

import argparse
import base64
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1]
ROOT = EXPERIMENT.parents[1]

sys.path.insert(0, str(EXPERIMENT / "tools"))
from prompts import CODE_TASKS, prompt  # noqa: E402

DATASET = "Gax-c/VeriContest"
DATA_REV = "3219d5f3739a79bd5bf02fc99ff3fa5095015a1b"
REPO_REV = "2578227514cdd625b1d3f8086c82b9f075ed5432"

PROBLEMS = {"lc121": 279, "lc217": 267, "lc242": 293}
IMAGE = "experiments/vericontest-qwen14b-experiment/sandbox/sandbox.sif"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def download(filename):
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Invalid dataset path: {filename}")

    destination = ROOT / ".cache/vericontest" / DATA_REV / relative

    if not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = (
            f"https://huggingface.co/datasets/{DATASET}/resolve/"
            f"{DATA_REV}/"
            + urllib.parse.quote(filename, safe="/")
        )
        print(f"Downloading {filename} ...", flush=True)

        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()

        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)

    return destination


def generate(source):
    source = source.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    if revision != REPO_REV:
        raise ValueError(
            f"Expected benchmark revision {REPO_REV}; found {revision}"
        )

    # Refuse to overwrite generated tasks while we are setting up manually.
    for pid in PROBLEMS:
        for setting in CODE_TASKS:
            target = EXPERIMENT / "tasks/vericontest" / f"{pid}--{setting}"
            if target.exists():
                raise FileExistsError(
                    f"Task already exists: {target}. "
                    "Inspect it before regenerating."
                )

    found = {}
    with download("problems.jsonl").open() as stream:
        for line in stream:
            if not line.strip():
                continue
            problem = json.loads(line)
            pid = problem.get("problem")
            if pid in PROBLEMS:
                if pid in found:
                    raise ValueError(f"Duplicate problem ID: {pid}")
                found[pid] = problem

    if set(found) != set(PROBLEMS):
        raise ValueError(f"Missing problems: {set(PROBLEMS) - set(found)}")

    # Validate all inputs before creating any task folders.
    prepared = {}
    for pid, expected_count in PROBLEMS.items():
        problem = found[pid]
        if problem["family"] != "leetcode" or problem.get("checker"):
            raise ValueError(f"Unsupported problem interface: {pid}")

        cases = [
            json.loads(line)
            for line in download(problem["testcases_path"]).read_text().splitlines()
            if line.strip()
        ]
        if len(cases) != expected_count:
            raise ValueError(
                f"{pid}: expected {expected_count} tests, found {len(cases)}"
            )

        oracle = (
            source / "benchmark/leetcode" / pid / "tests/reference_oracle.rs"
        ).read_text()

        if oracle.count('include!("../code.rs");') != 1:
            raise ValueError(f"Unsupported oracle layout: {pid}")

        for field in ("description", "spec", "code", "code_spec"):
            if not isinstance(problem.get(field), str) or not problem[field].strip():
                raise ValueError(f"{pid}: missing {field}")

        prepared[pid] = (problem, cases, oracle)

    manifest = {
        "dataset": DATASET,
        "dataset_revision": DATA_REV,
        "benchmark_revision": REPO_REV,
        "scope": "Three problems x three CodeGen settings",
        "prompt_source": "Reconstructed Colab pilot prompts",
        "formal_proof_checking": False,
        "task_names": [],
        "tasks": [],
    }

    for pid, (problem, cases, oracle) in prepared.items():
        # One byte-identical suite per problem, so git stores it once.
        suite = {
            "problem": pid,
            "tests": cases,
            "references": {
                "plain": problem["code"],
                "verus": problem["code_spec"],
            },
        }
        compressed = gzip.compress(
            json.dumps(suite, separators=(",", ":")).encode(),
            mtime=0,
        )

        for setting in CODE_TASKS:
            name = f"{pid}--{setting}"
            task = EXPERIMENT / "tasks/vericontest" / name

            for directory in ("environment", "tests", "solution"):
                (task / directory).mkdir(parents=True, exist_ok=True)

            text = prompt(setting, problem)

            # These markers match our one_shot.py adapter exactly.
            (task / "instruction.md").write_text(
                "<VERICONTEST_PROMPT>\n"
                + text
                + "\n</VERICONTEST_PROMPT>\n"
            )

            (task / "task.toml").write_text(
                'version = "1.0"\n\n'
                "[metadata]\n"
                f'problem = "{pid}"\n'
                f'setting = "{setting}"\n\n'
                "[verifier]\n"
                "timeout_sec = 2400.0\n\n"
                "[agent]\n"
                "timeout_sec = 720.0\n\n"
                "[environment]\n"
                f'docker_image = "{IMAGE}"\n'
                "cpus = 2\n"
                "memory_mb = 4096\n"
                "build_timeout_sec = 3600.0\n"
            )
            (task / "environment/.gitkeep").write_text("")

            (task / "tests/suite.json.gz").write_bytes(compressed)
            (task / "tests/setting.txt").write_text(setting + "\n")
            (task / "tests/reference_oracle.rs").write_text(oracle)

            for filename in ("grade.py", "test.sh"):
                shutil.copy2(
                    EXPERIMENT / "tools/verifier" / filename,
                    task / "tests" / filename,
                )
            (task / "tests/test.sh").chmod(0o755)

            # Harbor's oracle control writes a known reference answer.
            reference = (
                problem["code"] if setting == "nl2code"
                else problem["code_spec"]
            )
            encoded = base64.b64encode(
                ("```rust\n" + reference + "\n```").encode()
            ).decode()

            solve = task / "solution/solve.sh"
            solve.write_text(
                "#!/bin/bash\n"
                "set -euo pipefail\n"
                "mkdir -p /app\n"
                f"printf '%s' '{encoded}' | base64 --decode "
                "> /app/response.txt\n"
            )
            solve.chmod(0o755)

            manifest["task_names"].append(name)
            manifest["tasks"].append({
                "name": name,
                "problem": pid,
                "setting": setting,
                "test_count": len(cases),
                "prompt_sha256": sha256(text.encode()),
                "suite_sha256": sha256(compressed),
                "oracle_sha256": sha256(oracle.encode()),
            })
            print(f"Created {name}: {len(cases)} tests", flush=True)

    (EXPERIMENT / "tasks/manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(f"\nFinished: {len(manifest['task_names'])} Harbor tasks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-source", type=Path, required=True)
    args = parser.parse_args()
    generate(args.benchmark_source)
