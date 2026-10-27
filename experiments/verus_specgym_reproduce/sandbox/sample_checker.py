"""Choose collision-free sample log IDs, then execute the pinned upstream checker."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def checker_arguments(arguments: list[str]) -> list[str]:
    # Explicit IDs and config-file policies belong to the upstream CLI. Hidden
    # grading supplies its own run ID and is unaffected by this wrapper.
    if any(arg.split("=", 1)[0] in ("--run_id", "--config") for arg in arguments):
        return arguments
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--evaluate_sample_only", default="true")
    parser.add_argument("--PARENT_OF_DETAILED_SAVE_DIR", type=Path, default=Path("/home/attempts"))
    parser.add_argument(
        "--PARENT_OF_COMPRESSED_SAVE_DIR",
        type=Path,
        default=Path("/logs/agent/compressed_verdicts"),
    )
    options, _ = parser.parse_known_args(arguments)
    if options.evaluate_sample_only.lower() != "true":
        return arguments
    candidate = 1
    while any(
        (parent / str(candidate)).exists()
        for parent in (
            options.PARENT_OF_DETAILED_SAVE_DIR,
            options.PARENT_OF_COMPRESSED_SAVE_DIR,
        )
    ):
        candidate += 1
    return [*arguments, f"--run_id={candidate}"]


if __name__ == "__main__":
    upstream = "/opt/verus_gym_specgen_check_runtime/bin/verus_gym_specgen_check"
    os.execv(upstream, [upstream, *checker_arguments(sys.argv[1:])])
