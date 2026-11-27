"""Run one HumanEval problem's hidden tests against /app/solution.py."""

from __future__ import annotations

import pathlib
import sys


def main() -> int:
    solution_path = pathlib.Path("/app/solution.py")
    if not solution_path.is_file():
        print("missing /app/solution.py", file=sys.stderr)
        return 1
    entry_point = pathlib.Path("/tests/entry_point.txt").read_text().strip()
    program = "\n".join(
        (
            solution_path.read_text(),
            pathlib.Path("/tests/hidden_test.txt").read_text(),
            f"check({entry_point})",
        )
    )
    try:
        exec(program, {})
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
