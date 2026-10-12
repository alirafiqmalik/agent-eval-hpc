#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
uv_bin=${UV:-uv}
if [[ -z "${UV:-}" && -x "$project_root/.cache/setup-tools/bin/uv" ]]; then
    uv_bin="$project_root/.cache/setup-tools/bin/uv"
fi
exec "${uv_bin}" run --project "$project_root" --locked python "$project_root/scripts/unity_eval.py" run "$@"
