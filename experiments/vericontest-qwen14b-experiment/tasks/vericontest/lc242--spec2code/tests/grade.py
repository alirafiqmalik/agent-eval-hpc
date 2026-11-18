"""Compile and test generated solutions for the VeriContest CodeGen pilot."""

import gzip
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

VERUS = "/opt/verus/verus"
COMPILE_TIMEOUT = 120


def extract(raw):
    blocks = re.findall(r"```rust\s*\n(.*?)```", raw, re.S)
    return blocks[0].strip() if len(blocks) == 1 else None


def package(code, oracle):
    marker = 'include!("../code.rs");'
    if oracle.count(marker) != 1:
        raise RuntimeError("Unexpected reference-oracle layout")

    harness = oracle.split(marker, 1)[1]
    uses_verus = "verus!" in code or "vstd::" in code

    # Avoid duplicate extension methods supplied by Verus.
    if uses_verus and "trait StrExt {" in harness:
        start = harness.index("trait StrExt {")
        end = harness.index("fn find_key", start)
        harness = harness[:start] + harness[end:]

    code = re.sub(r"\bfn\s+main\s*\(\s*\)\s*\{\s*\}", "", code)
    if not re.search(r"\bstruct\s+Solution\b", code):
        code = "pub struct Solution;\n" + code

    return code + "\n" + harness, uses_verus


def judge(code, suite, oracle, folder):
    if code is None:
        return {
            "status": "fail",
            "category": "Syntax Error",
            "tests_passed": 0,
            "detail": "Expected exactly one rust code block",
        }

    source, uses_verus = package(code, oracle)
    source_path = folder / "candidate.rs"
    binary = folder / "candidate"
    source_path.write_text(source, encoding="utf-8")

    # Both compilers optimize, so a Verus answer gets the same per-test limit.
    if uses_verus:
        command = [
            VERUS,
            "--no-verify",
            "--compile",
            "-O",
            str(source_path),
            "-o",
            str(binary),
        ]
    else:
        command = [
            "rustc",
            "--edition",
            "2021",
            "-O",
            str(source_path),
            "-o",
            str(binary),
        ]

    try:
        compiled = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as error:
        # TimeoutExpired keeps captured output as bytes, even in text mode.
        output = b"".join(
            part if isinstance(part, bytes) else part.encode()
            for part in (error.stdout, error.stderr)
            if part
        )
        (folder / "compile.log").write_bytes(output)
        return {
            "status": "fail",
            "category": "Compile Timeout",
            "tests_passed": 0,
        }

    (folder / "compile.log").write_text(
        compiled.stdout + compiled.stderr,
        encoding="utf-8",
    )

    if compiled.returncode:
        return {
            "status": "fail",
            "category": "Syntax Error",
            "tests_passed": 0,
        }

    if not binary.is_file():
        raise RuntimeError("Compiler returned success without a binary")

    passed = 0
    for index, case in enumerate(suite["tests"]):
        try:
            execution = subprocess.run(
                [str(binary)],
                input=json.dumps(case["input"]) + "\n",
                capture_output=True,
                text=True,
                timeout=2,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "fail",
                "category": "Timeout",
                "tests_passed": passed,
                "failed_case": index,
            }

        try:
            observed = json.loads(execution.stdout.strip())["output"]
        except (ValueError, KeyError, TypeError):
            observed = object()

        if execution.returncode or observed != case["output"]:
            (folder / "execution.log").write_text(
                execution.stdout + "\n" + execution.stderr,
                encoding="utf-8",
            )
            return {
                "status": "fail",
                "category": "Incorrect Output",
                "tests_passed": passed,
                "failed_case": index,
            }

        passed += 1

    return {"status": "pass", "category": None, "tests_passed": passed}


def main():
    tests = Path("/tests")
    logs = Path("/logs/verifier")
    logs.mkdir(parents=True, exist_ok=True)
    reward = logs / "reward.txt"
    reward.unlink(missing_ok=True)

    # The three settings of one problem share a byte-identical suite, so git
    # stores it once. The setting is in its own file.
    suite = json.loads(gzip.decompress((tests / "suite.json.gz").read_bytes()))
    setting = (tests / "setting.txt").read_text().strip()
    if not suite.get("tests"):
        raise RuntimeError("Empty test suite")
    if set(suite.get("references", {})) != {"plain", "verus"}:
        raise RuntimeError("Both plain and Verus references are required")

    oracle = (tests / "reference_oracle.rs").read_text()
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="vericontest-") as temporary:
        temporary = Path(temporary)

        for label, code in suite["references"].items():
            folder = temporary / label
            folder.mkdir()
            result = judge(code, suite, oracle, folder)
            (logs / f"reference-{label}.json").write_text(json.dumps(result, indent=2))
            (logs / f"reference-{label}.log").write_bytes((folder / "compile.log").read_bytes())
            if result["status"] != "pass":
                raise RuntimeError(f"Reference validation failed: {label}: {result}")

        negative = temporary / "negative"
        negative.mkdir()
        result = judge(
            "impl Solution { NOT RUST }",
            suite,
            oracle,
            negative,
        )
        if result["category"] != "Syntax Error":
            raise RuntimeError("Syntax negative control failed")

        if suite["problem"] == "lc121":
            wrong = "impl Solution { pub fn max_profit(prices: Vec<i32>) -> i32 { 0 } }"
            result = judge(wrong, suite, oracle, negative)
            if result["category"] != "Incorrect Output":
                raise RuntimeError("Output negative control failed")

        response = Path("/app/response.txt")
        if not response.is_file():
            raise RuntimeError("Agent submission is missing")

        folder = temporary / "submission"
        folder.mkdir()
        result = judge(extract(response.read_text()), suite, oracle, folder)

        for name in ("candidate.rs", "compile.log", "execution.log"):
            path = folder / name
            if path.is_file():
                (logs / name).write_bytes(path.read_bytes())

    result.update(
        problem=suite["problem"],
        task=setting,
        test_count=len(suite["tests"]),
        evaluation_seconds=time.perf_counter() - started,
    )
    (logs / "judgment.json").write_text(json.dumps(result, indent=2))
    reward.write_text("1\n" if result["status"] == "pass" else "0\n")


if __name__ == "__main__":
    main()
