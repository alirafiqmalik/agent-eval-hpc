#!/usr/bin/env bash
# Install the same pinned inference revision on Unity's Grace ARM CPUs.
set -euo pipefail
umask 077
runtime=${1:?ARM runtime directory required}
[[ $(uname -m) == aarch64 ]]
mkdir -p "${runtime}/bin"
exec 9>"${runtime}/setup.lock"
flock 9
if [[ ! -x "${runtime}/bin/uv" ]]; then
    curl --fail --location --retry 3 https://github.com/astral-sh/uv/releases/download/0.11.2/uv-aarch64-unknown-linux-gnu.tar.gz \
        | tar -xz --strip-components=1 -C "${runtime}/bin" uv-aarch64-unknown-linux-gnu/uv
fi
export UV_CACHE_DIR="${runtime}/uv-cache"
if [[ ! -x "${runtime}/.venv/bin/python" ]]; then
    "${runtime}/bin/uv" venv --python /usr/bin/python3 "${runtime}/.venv"
fi
"${runtime}/bin/uv" pip install --python "${runtime}/.venv/bin/python" --prerelease allow \
    --extra-index-url https://wheels.vllm.ai/3fe917abb13160d60bea5330399f5c09ca282348 \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu130 --index-strategy unsafe-best-match \
    'vllm==0.31.1rc1.dev289+g3fe917abb' 'transformers==5.19.0' pyyaml
"${runtime}/.venv/bin/python" - <<'PY'
import importlib.metadata, json, os, platform
from pathlib import Path
import torch
assert torch.cuda.is_available()
record = {"architecture": platform.machine(), "packages": {
    p: importlib.metadata.version(p) for p in ("vllm", "torch", "transformers")},
    "gpu": torch.cuda.get_device_name(0), "memory_bytes": torch.cuda.get_device_properties(0).total_memory}
Path(os.environ['HPC_OUTPUT_DIR'], 'arm-runtime.json').write_text(json.dumps(record, indent=2)+'\n')
PY
