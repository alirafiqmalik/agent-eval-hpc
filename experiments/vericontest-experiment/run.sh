#!/usr/bin/env bash
# Run VeriContest on one Unity GPU type with the project's make targets.
# Usage: ./experiments/vericontest-experiment/run.sh {plan|evaluate|collect} {a100|l40s} RUN_ID [JOB]
# JOB is job.yaml (default), smoke.yaml, or oracle.yaml. Use a new RUN_ID for each evaluate.
# collect resumes an evaluate that stopped, with the same GPU and RUN_ID.
set -euo pipefail
[[ $# -ge 3 && $# -le 4 ]] || { sed -n 3,5p "$0" | cut -c3- >&2; exit 2; }
action=$1 gpu=$2 run_id=$3 job=${4:-job.yaml}
case "${action}" in plan | evaluate | collect) ;; *) echo "unknown action: ${action}" >&2; exit 2 ;; esac
experiment=experiments/vericontest-experiment
case "${gpu}" in
    a100) runner_file= ;;
    l40s) runner_file=${experiment}/runner-l40s.yaml ;;
    *) echo "unknown GPU: ${gpu} (use a100 or l40s)" >&2; exit 2 ;;
esac
cd "$(dirname -- "${BASH_SOURCE[0]}")/../.."
exec make --no-print-directory "${action}" RUN_ID="${run_id}" \
    EXPERIMENT="${experiment}/${job}" \
    RUNNER_CONFIG="src/runners/unityhpc/connections/vericontest-${gpu}.yaml" \
    RUNNER_FILE="${runner_file}"
