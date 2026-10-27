"""Remap the pinned upstream Harbor ZIPs without changing benchmark inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1]
REPO = EXPERIMENT.parents[1]
COMMON = ("common_artifacts", "evaluator_scripts", "runtime_bundle")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack_context(source: Path, target: Path) -> None:
    # Many tiny snippet files are expensive to stage on Unity's shared storage.
    # Preserve their bytes in one deterministic archive per task.
    with (
        target.open("wb") as stream,
        gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in [source, *sorted(source.rglob("*"))]:
                info = archive.gettarinfo(
                    path, arcname=str(Path("task") / path.relative_to(source))
                )
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as content:
                        archive.addfile(info, content)
                else:
                    archive.addfile(info)


def import_task(archive: Path, destination: Path) -> dict:
    name = archive.stem
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts or path.parts[0] != name:
                    raise ValueError(f"unsafe task archive member: {path}")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"task archive contains a symlink: {path}")
            bundle.extractall(root)
        source = root / name
        context = source / "environment/build_context"
        common_path = EXPERIMENT / "sandbox/upstream-common.tar.gz"
        signatures = {
            str(p.relative_to(context)): digest(p)
            for folder in COMMON
            for p in sorted((context / folder).rglob("*"))
            if p.is_file()
        }
        signatures_path = EXPERIMENT / "sandbox/common-sha256.json"
        if not common_path.exists():
            with tarfile.open(common_path, "w:gz") as bundle:
                for folder in COMMON:
                    bundle.add(context / folder, arcname=folder)
            signatures_path.write_text(json.dumps(signatures, indent=2) + "\n")
        elif json.loads(signatures_path.read_text()) != signatures:
            raise ValueError(f"upstream common runtime differs in {name}; refusing to mix versions")

        task = destination / name
        if task.exists():
            shutil.rmtree(task)
        (task / "environment/files").mkdir(parents=True)
        for filename in ("instruction.md", "task.toml"):
            shutil.copyfile(source / filename, task / filename)
        for folder in ("tests", "solution"):
            shutil.copytree(source / folder, task / folder)
        pack_context(context / "task_specific_artifacts", task / "environment/files/context.tar.gz")
        shutil.copyfile(EXPERIMENT / "sandbox/setup.sh", task / "environment/files/setup.sh")
        # Harbor reads WORKDIR from this file even when using a prebuilt SIF.
        (task / "environment/Dockerfile").write_text("FROM scratch\nWORKDIR /workspace\n")
        config_path = task / "task.toml"
        config = config_path.read_text().replace(
            "[environment]\n",
            "[environment]\n"
            'docker_image = "experiments/verus_specgym_reproduce/sandbox/sandbox.sif"\n',
        )
        config_path.write_text(config)
        for filename in ("test.sh", "grade.py"):
            shutil.copyfile(EXPERIMENT / "tools/verifier" / filename, task / "tests" / filename)
        for script in (task / "tests/test.sh", task / "solution/solve.sh"):
            script.chmod(0o755)
        # Count hidden tests from the original manifest, independent of model outputs.
        hidden = task / "tests/task_testing_specific_artifacts.tar.gz"
        with tarfile.open(hidden) as bundle:
            member = next(m for m in bundle.getmembers() if m.name.endswith("manifest.json"))
            manifest = json.load(io.TextIOWrapper(bundle.extractfile(member)))
        return {
            "task": name,
            "archive_sha256": digest(archive),
            "instruction_sha256": digest(task / "instruction.md"),
            "hidden_sha256": digest(hidden),
            "bucket_totals": {
                bucket: len(payload["passed_test_ids"])
                for bucket, payload in manifest["snippets"].items()
            },
        }


def positive_controls() -> None:
    """Independent domino-counting control, outside the scored dataset."""
    control = EXPERIMENT / "tasks/verus-specgym/verus-gym-50-a"
    if not control.is_dir():
        return
    template = (control / "solution/solve_template.rs").read_text()
    for name, expression in (
        ("pre_spec", "1 <= in1.m && in1.m <= in1.n && in1.n <= 16"),
        ("post_spec", "out.max_dominoes == (in1.m * in1.n) / 2"),
    ):
        template, count = re.subn(
            rf"(pub open spec fn {name}\([^)]*\) -> bool\s*\{{)\s*\}}",
            rf"\1\n            {expression}\n        }}",
            template,
        )
        if count != 1:
            raise ValueError("Positive control template shape changed")
    for kind in ("known-correct", "delayed-correct", "repeated-checker"):
        target = EXPERIMENT / "tasks/diagnostics" / kind / control.name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(control, target)
        (target / "solution/attempted_solve.rs").write_text(template)
        if kind == "delayed-correct":
            script = target / "solution/solve.sh"
            script.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                "# Cross the former container transport limit inside Harbor.\n"
                "sleep 610\ncp /solution/attempted_solve.rs /home/dev/solve.rs\n"
            )
        elif kind == "repeated-checker":
            (target / "solution/solve.sh").write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                "cp /solution/attempted_solve.rs /home/dev/solve.rs\n"
                "verus_gym_specgen_check > /logs/agent/checker-first.txt 2>&1\n"
                "sha256sum /logs/agent/compressed_verdicts/1/results.zip "
                "> /logs/agent/first-check.sha256\n"
                "rm -r /home/attempts\n"
                "verus_gym_specgen_check > /logs/agent/checker-second.txt 2>&1\n"
                "test -s /logs/agent/compressed_verdicts/2/results.zip\n"
                "sha256sum -c /logs/agent/first-check.sha256\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--allow-partial", action="store_true", help="Bootstrap smoke tasks only")
    args = parser.parse_args()
    ids = json.loads((EXPERIMENT / "tasks/task_ids.json").read_text())
    expected = ["verus-gym-" + p.replace("__", "-").lower() for p in ids]
    destination = EXPERIMENT / "tasks/verus-specgym"
    records = []
    missing = []
    for name in expected:
        archive = args.zip_dir / f"{name}.zip"
        if not archive.is_file():
            missing.append(name)
            continue
        records.append(import_task(archive, destination))
    manifest = {
        "expected": len(expected),
        "imported": len(records),
        "missing": missing,
        "tasks": records,
    }
    (EXPERIMENT / "tasks/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Two verifier controls, kept outside the scored 581-task dataset. The
    # deliberately malformed submission must fail syntax; an all-true spec must
    # accept completeness cases and reject none of the soundness cases.
    control = destination / "verus-gym-1009-a"
    if control.is_dir():
        shutil.rmtree(EXPERIMENT / "tasks/diagnostics", ignore_errors=True)
        for kind in ("invalid-syntax", "accept-all"):
            target = EXPERIMENT / "tasks/diagnostics" / kind / control.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(control, target)
            if kind == "accept-all":
                with tarfile.open(control / "environment/files/context.tar.gz") as bundle:
                    template = bundle.extractfile("task/solve_template.rs").read().decode()
                spec, count = re.subn(r"(-> bool\s*\{)\s*\}", r"\1 true }", template)
                if count != 6:
                    raise ValueError("Verifier control template shape changed")
                (target / "solution/attempted_solve.rs").write_text(spec)
            else:
                (target / "solution/attempted_solve.rs").write_text("not a Verus specification;\n")
    positive_controls()
    print(f"Imported {len(records)}/{len(expected)} tasks")
    if missing and not args.allow_partial:
        raise SystemExit("Incomplete dataset; full runs are disabled")


if __name__ == "__main__":
    main()
