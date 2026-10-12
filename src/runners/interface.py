"""Small runner interface, independent of scheduler and controller schemas."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ExecutionNotFoundError(LookupError):
    """The backend authoritatively reports that an execution identity is absent."""


class ExecutionConflictError(ValueError):
    """An existing execution identity belongs to a different immutable request."""


@dataclass(frozen=True)
class ExecutionRequest:
    id: str
    bundle: Path
    argv: tuple[str, ...]
    outputs: tuple[str, ...]
    hardware: dict
    walltime_seconds: int
    max_attempts: int = 1
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", self.id):
            raise ValueError("invalid execution identity")
        if not self.argv or any(not a or "\0" in a for a in self.argv):
            raise ValueError("execution requires a nonempty argument vector")
        if self.walltime_seconds <= 0 or not 1 <= self.max_attempts <= 2:
            raise ValueError("invalid execution walltime or attempt budget")
        if not self.outputs or any(
            Path(p).is_absolute() or ".." in Path(p).parts or p in ("", ".") for p in self.outputs
        ):
            raise ValueError("outputs must be explicit relative file paths")


def request_digest(request: ExecutionRequest) -> str:
    """Identify the portable controls of one request."""
    controls = {
        "id": request.id,
        "bundle": str(request.bundle.resolve()),
        "argv": list(request.argv),
        "outputs": list(request.outputs),
        "hardware": request.hardware,
        "walltime_seconds": request.walltime_seconds,
        "max_attempts": request.max_attempts,
        "environment": request.environment,
    }
    return hashlib.sha256(json.dumps(controls, sort_keys=True).encode()).hexdigest()


class Runner(Protocol):
    idempotent_submit: bool

    def plan(self, request: ExecutionRequest) -> dict: ...
    def submit(self, request: ExecutionRequest) -> dict: ...
    def status(self, identity: str) -> dict: ...
    def attest(self, request: ExecutionRequest, observation: dict) -> dict: ...
    def cancel(self, identity: str) -> dict: ...
    def fetch(self, identity: str, destination: Path) -> list[dict]: ...
