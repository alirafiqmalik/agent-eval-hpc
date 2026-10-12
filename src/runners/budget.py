"""Conservative, durable allocation reservations across submissions."""

from __future__ import annotations

import fcntl
import json
import math
from contextlib import contextmanager
from pathlib import Path


class Budget:
    def __init__(self, path: Path, *, gpu_hours: float | None, cpu_hours: float | None):
        self.path = path
        hours = {"gpu_seconds": gpu_hours, "cpu_seconds": cpu_hours}
        if any(v is not None and (not math.isfinite(v) or v < 0) for v in hours.values()):
            raise ValueError("allocation limits must be finite nonnegative hours or null")
        self.limits = {
            key: value * 3600 if value is not None else None for key, value in hours.items()
        }

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield data
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2))
            temporary.replace(self.path)

    def reserve(self, identity: str, gpu_count: int, walltime_seconds: int, attempts: int = 1):
        if walltime_seconds <= 0 or attempts <= 0 or gpu_count < 0:
            raise ValueError("invalid allocation budget")
        rounded = math.ceil(walltime_seconds / 60) * 60
        reservation = {
            "gpu_seconds": gpu_count * rounded * attempts,
            "cpu_seconds": 0 if gpu_count else rounded * attempts,
        }
        with self._locked() as data:
            if identity in data:
                existing = data[identity].get("reservation", data[identity])
                if existing != reservation:
                    raise ValueError("identity already has a different budget reservation")
                return
            for key, limit in self.limits.items():
                if (
                    limit is not None
                    and sum(r[key] for r in data.values()) + reservation[key] > limit
                ):
                    raise ValueError(f"{key} budget exhausted")
            data[identity] = reservation

    def settle(self, identity: str, gpu_count: int, elapsed_seconds: int, evidence: dict):
        """Replace a hold only after a runner proves one terminal allocation."""
        if gpu_count < 0 or elapsed_seconds < 0 or not evidence:
            raise ValueError("invalid accounted allocation")
        # Round upward to allocation minutes and retain the original hold.
        charged = max(60, math.ceil(elapsed_seconds / 60) * 60)
        usage = {
            "gpu_seconds": gpu_count * charged,
            "cpu_seconds": 0 if gpu_count else charged,
        }
        with self._locked() as data:
            if identity not in data:
                raise ValueError("allocation has no reservation")
            original = data[identity].get("reservation", data[identity])
            if bool(original["gpu_seconds"]) != bool(gpu_count):
                raise ValueError("accounted allocation differs from its CPU/GPU reservation")
            previous = data[identity].get("settlement")
            settlement = {"elapsed_seconds": elapsed_seconds, "evidence": evidence}
            if previous is not None and previous != settlement:
                raise ValueError("accounted allocation evidence changed")
            data[identity] = {**usage, "reservation": original, "settlement": settlement}
            overrun = {key: max(0, usage[key] - original[key]) for key in self.limits}
            if any(overrun.values()):
                # Scheduler teardown can extend accounting beyond the requested limit.
                # Record measured use; the next reservation still enforces any finite cap.
                data[identity]["reservation_overrun"] = overrun

    def release_unsubmitted(self, identity: str):
        """Release only after a definite rejection, never after a transport timeout."""
        with self._locked() as data:
            data.pop(identity, None)
