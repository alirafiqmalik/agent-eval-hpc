#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
uv_bin=${UV:-uv}
if [[ -z "${UV:-}" && -x "${root}/.cache/setup-tools/bin/uv" ]]; then
    uv_bin="${root}/.cache/setup-tools/bin/uv"
fi
cd "${root}"
exec "${uv_bin}" run --locked --group plots python experiments/verus_specgym_reproduce/tools/batch.py "$@"
