"""Stage model metadata locally so optional-file lookups do not block on NFS."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def stage_metadata(source: Path, destination: Path) -> dict:
    """Copy metadata verbatim; keep large checkpoint files on their original storage."""
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Local model metadata requires an existing model directory")
    files = []
    for directory, folders, names in os.walk(source):
        # Hugging Face download locks and cache records are not model inputs.
        folders[:] = sorted(folder for folder in folders if folder != ".cache")
        for name in sorted(names):
            original = Path(directory) / name
            relative = original.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            size = original.stat().st_size
            record = {"path": str(relative), "bytes": size}
            if original.suffix in {".safetensors", ".bin", ".pt", ".pth", ".gguf"}:
                target.symlink_to(original)
                record["mode"] = "original-weight-symlink"
            else:
                shutil.copyfile(original, target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != hashlib.sha256(original.read_bytes()).hexdigest():
                    raise RuntimeError(f"Model metadata changed while staging: {relative}")
                record.update(mode="local-copy", sha256=digest)
            files.append(record)
    return {"source": str(source), "destination": str(destination), "files": files}
