"""Combine Harbor trial transcripts into one JSONL file."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def export(logs: Path) -> int:
    destination = logs / "trajectory.jsonl"
    lines: list[str] = []
    if logs.is_dir():
        for path in sorted(logs.rglob("trajectory.json")):
            if path.parent.name != "agent":
                continue
            trial = path.parent.parent.relative_to(logs).as_posix()
            record = {"trial": trial, "trajectory": json.loads(path.read_text())}
            lines.append(json.dumps(record))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + ("\n" if lines else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: export.py LOGS_DIR", file=sys.stderr)
        return 2
    return export(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
