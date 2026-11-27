"""Check that the sandbox gives the requested CPUs and memory.

gVisor reports host values for nproc and /proc/meminfo, so this probe measures:
the requested processes must run in parallel, and most of the requested memory
must be writable. A smaller memory limit stops the probe with exit code 137.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sys
import time

ROUNDS = 1_000_000
MIN_SPEEDUP_PER_CPU = 0.6


def work(seed: int) -> str:
    digest = seed.to_bytes(8, "little")
    for _ in range(ROUNDS):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


def probe(cpus: int, memory_mb: int) -> dict:
    size = memory_mb * 1024 * 1024 * 3 // 4
    block = bytearray(size)
    for offset in range(0, size, 4096):
        block[offset] = 1
    touched_mb = sum(block[::4096]) * 4096 // (1024 * 1024)
    del block
    start = time.monotonic()
    serial = work(0)
    serial_seconds = time.monotonic() - start
    start = time.monotonic()
    with multiprocessing.Pool(cpus) as pool:
        parallel = pool.map(work, [0] * cpus)
    parallel_seconds = time.monotonic() - start
    speedup = cpus * serial_seconds / parallel_seconds
    report = {
        "requested_cpus": cpus,
        "requested_memory_mb": memory_mb,
        "touched_memory_mb": touched_mb,
        "serial_seconds": round(serial_seconds, 3),
        "parallel_seconds": round(parallel_seconds, 3),
        "speedup": round(speedup, 2),
        "results_match": all(result == serial for result in parallel),
    }
    report["passed"] = (
        report["results_match"]
        and touched_mb >= memory_mb * 3 // 4 - 1
        and speedup >= MIN_SPEEDUP_PER_CPU * cpus
    )
    return report


if __name__ == "__main__":
    config = json.loads(open(sys.argv[1]).read())
    result = probe(config["cpus"], config["memory_mb"])
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
