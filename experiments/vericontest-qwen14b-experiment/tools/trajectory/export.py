"""Combine saved generation records into one JSONL file."""

import json
import sys
from pathlib import Path


def export(logs):
    logs.mkdir(parents=True, exist_ok=True)
    destination = logs / "trajectory.jsonl"

    with destination.open("w", encoding="utf-8") as stream:
        for path in sorted(logs.rglob("generation.json")):
            if path.parent.name != "agent":
                continue

            record = {
                "trial": path.parent.parent.relative_to(logs).as_posix(),
                "generation": json.loads(path.read_text()),
            }
            for filename in ("request.json", "response.json"):
                artifact = path.parent / filename
                if artifact.is_file():
                    record[filename.removesuffix(".json")] = json.loads(artifact.read_text())

            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export.py LOGS_DIR")
    raise SystemExit(export(Path(sys.argv[1])))
