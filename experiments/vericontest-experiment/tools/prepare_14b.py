"""Prepare separate, revision-pinned Qwen 14B configurations."""

import json
import re
import urllib.request
from pathlib import Path

import yaml

experiment = Path(__file__).resolve().parents[1]
model = "Qwen/Qwen2.5-Coder-14B-Instruct"

# Resolve the model's own revision, rather than reusing the 7B revision.
url = f"https://huggingface.co/api/models/{model}"
with urllib.request.urlopen(url, timeout=30) as response:
    revision = json.load(response)["sha"]

if not re.fullmatch(r"[0-9a-f]{40}", revision):
    raise ValueError(f"Unexpected model revision: {revision}")

runner = yaml.safe_load((experiment / "runner.yaml").read_text())
runner["model_server"]["command"] = (
    "env VLLM_USE_FLASHINFER_SAMPLER=0 "
    "vllm serve {model} "
    f"--revision {revision} "
    "--served-model-name vericontest "
    "--port {port} "
    "--tensor-parallel-size 1 "
    "--max-model-len {context_length} "
    "--dtype half"
)

outputs = {
    "agent/agent-config-14b.yaml": {"model": model},
    "runner-a100-14b.yaml": runner,
}

for relative in outputs:
    if (experiment / relative).exists():
        raise FileExistsError(f"Already exists: {relative}")

for relative, data in outputs.items():
    (experiment / relative).write_text(yaml.safe_dump(data, sort_keys=False))
    print("Created:", relative)

print("Model:", model)
print("Pinned revision:", revision)
print("Precision: FP16")
print("No model weights downloaded and no job submitted.")
