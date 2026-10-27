#!/usr/bin/env bash
set -euo pipefail
experiment=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export UNITYHPC_CONTROLLER_TIMEOUT="${UNITYHPC_CONTROLLER_TIMEOUT:-180}"
exec "${experiment}/run-all.sh" --matrix "${experiment}/acceleration.yaml" "$@"
