"""Download pinned model files on a CPU allocation, outside the project quota."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import yaml
from huggingface_hub import snapshot_download


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--model", action="append")
    args = parser.parse_args()
    experiment = Path(__file__).resolve().parents[1]
    models = yaml.safe_load((experiment / "reproduction.yaml").read_text())["models"]
    args.cache.mkdir(parents=True, exist_ok=True)
    receipts = {}
    for name in args.model or models:
        model = models[name]
        destination = args.cache / name
        marker = destination / "download-complete.json"
        index_path = destination / "model.safetensors.index.json"
        shards_complete = index_path.is_file() and all(
            (destination / name).is_file() and (destination / name).stat().st_size > 0
            for name in set(json.loads(index_path.read_text())["weight_map"].values())
        )
        if (
            marker.is_file()
            and shards_complete
            and json.loads(marker.read_text())["revision"] == model["revision"]
        ):
            receipts[name] = json.loads(marker.read_text())
            continue
        existing = sum(p.stat().st_size for p in destination.rglob("*.safetensors"))
        needed = max(0, model["weight_bytes"] - existing) + 10 * 1024**3
        if shutil.disk_usage(args.cache).free < needed:
            raise RuntimeError(f"Insufficient scratch space for {name}: need {needed} bytes")
        print(f"Downloading {name} revision {model['revision']}", flush=True)
        snapshot_download(
            model["repository"],
            revision=model["revision"],
            local_dir=destination,
            allow_patterns=["*.safetensors", "*.json", "*.py", "*.jinja", "*.model", "*.tiktoken"],
            max_workers=8,
        )
        index = json.loads((destination / "model.safetensors.index.json").read_text())
        if not all((destination / name).is_file() for name in set(index["weight_map"].values())):
            raise RuntimeError(f"Missing weight shards for {name}")
        receipts[name] = {
            "repository": model["repository"],
            "revision": model["revision"],
            "path": str(destination),
        }
        marker.write_text(json.dumps(receipts[name], indent=2) + "\n")
        print(f"Completed {name}", flush=True)
    output = Path(os.environ["HPC_OUTPUT_DIR"])
    (output / "model-downloads.json").write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    main()
