#!/usr/bin/env bash
# Build the experiment image, serve the model once, then start Harbor.
set -euo pipefail
umask 077

job_config="${1:?Harbor job config path required}"
output_dir="${HPC_OUTPUT_DIR:?controller output directory missing}"

# Unity has user namespaces but no subordinate UID mappings. Its host fakeroot
# library needs GLIBC_2.38, which is absent from Debian bookworm task images.
# Keep the root-mapped namespace and avoid injecting that host library.
export APPTAINER_IGNORE_FAKEROOT_COMMAND=1

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while [[ ! -f "${repo_root}/pyproject.toml" && "${repo_root}" != "/" ]]; do
    repo_root=$(dirname "${repo_root}")
done
cd "${repo_root}"

if [[ "${job_config}" != /* ]]; then
    job_config="${repo_root}/${job_config}"
fi
experiment_dir=$(dirname "${job_config}")

if [[ -n "${EVAL_RUNTIME_ROOT:-}" && -x "${EVAL_RUNTIME_ROOT}/.venv/bin/harbor" ]]; then
    export PATH="${EVAL_RUNTIME_ROOT}/.venv/bin:${PATH}"
fi
if ! command -v harbor >/dev/null 2>&1; then
    printf '%s\n' 'harbor is not installed in this allocation' >&2
    exit 1
fi
if [[ -n "${EVAL_RUNTIME_ROOT:-}" && -x "${EVAL_RUNTIME_ROOT}/.venv/bin/python" ]]; then
    python="${EVAL_RUNTIME_ROOT}/.venv/bin/python"
else
    python=python3
fi
if [[ -n "${EVAL_RUNTIME_ROOT:-}" ]]; then
    export HF_HOME="${EVAL_RUNTIME_ROOT}/hf"
    mkdir -p "${HF_HOME}"
    cuda_root="${EVAL_RUNTIME_ROOT}/.venv/lib/python3.12/site-packages/nvidia/cu13"
    if [[ -x "${cuda_root}/bin/nvcc" ]]; then
        export CUDA_HOME="${cuda_root}"
        export PATH="${cuda_root}/bin:${PATH}"
    fi
fi

definition="${experiment_dir}/sandbox/sandbox.def"
image="${output_dir}/sandbox.sif"
if [[ -n "${EVAL_RUNTIME_ROOT:-}" ]]; then
    cached_image="${EVAL_RUNTIME_ROOT}/sandbox.sif"
    cached_hash="${EVAL_RUNTIME_ROOT}/sandbox.def.sha256"
    definition_hash=$(sha256sum "${definition}" | cut -d ' ' -f 1)
    if [[ -s "${cached_image}" && -f "${cached_hash}" ]] &&
        [[ $(cat "${cached_hash}") == "${definition_hash}" ]]; then
        image="${cached_image}"
    fi
fi
if [[ ! -s "${image}" ]]; then
    if command -v apptainer >/dev/null 2>&1; then
        builder=apptainer
    elif command -v singularity >/dev/null 2>&1; then
        builder=singularity
    elif type module >/dev/null 2>&1; then
        module load apptainer || module load singularity
        if command -v apptainer >/dev/null 2>&1; then
            builder=apptainer
        else
            builder=singularity
        fi
    else
        printf '%s\n' 'apptainer is not available in this allocation' >&2
        exit 1
    fi
    "${builder}" build "${image}" "${definition}"
fi

# Trial containers repeatedly read the image. Keep that traffic on node-local
# scratch instead of the shared filesystem for the duration of this allocation.
local_image_dir=""
if [[ -n "${TMPDIR:-}" && -d "${TMPDIR}" ]]; then
    local_image_dir=$(mktemp -d "${TMPDIR%/}/agentic-sfv-image.XXXXXXXX")
    cp -- "${image}" "${local_image_dir}/sandbox.sif"
    image="${local_image_dir}/sandbox.sif"
fi

server_script="${output_dir}/model-server.sh"
mkdir -p "${output_dir}/logs" "${output_dir}/results"
"${python}" - "${experiment_dir}" "${server_script}" <<'PY'
import sys
from pathlib import Path

import yaml

experiment = Path(sys.argv[1])
agent = yaml.safe_load((experiment / "agent/agent-config.yaml").read_text())
runner = yaml.safe_load((experiment / "runner.yaml").read_text())
server = runner["model_server"]
command = server["command"].format(
    model=agent["model"],
    port=server["port"],
    context_length=server["context_length"],
)
Path(sys.argv[2]).write_text("exec " + command + "\n")
Path(str(sys.argv[2]) + ".port").write_text(str(server["port"]) + "\n")
PY
port=$(tr -d '[:space:]' < "${server_script}.port")

server_pid=""
cleanup_pid=""
cleanup() {
    if [[ -n "${cleanup_pid}" ]]; then
        kill "${cleanup_pid}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${server_pid}" ]]; then
        kill "${server_pid}" >/dev/null 2>&1 || true
    fi
    tar -C "${output_dir}" -czf "${output_dir}/harbor-jobs.tar.gz" logs results || true
    if [[ -n "${local_image_dir}" ]]; then
        rm -rf -- "${local_image_dir}"
    fi
}
trap cleanup EXIT

bash "${server_script}" >"${output_dir}/logs/model-server.log" 2>&1 &
server_pid=$!
ready=0
for _ in $(seq 1 180); do
    if command -v curl >/dev/null 2>&1 && curl -sf "http://127.0.0.1:${port}/v1/models" >/dev/null; then
        ready=1
        break
    fi
    if ! kill -0 "${server_pid}" >/dev/null 2>&1; then
        break
    fi
    sleep 10
done
if [[ "${ready}" != 1 ]]; then
    printf '%s\n' 'model server did not become ready' >&2
    exit 1
fi

export OPENAI_BASE_URL="http://127.0.0.1:${port}/v1"
export OPENAI_API_KEY=local
export PYTHONPATH="${repo_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    /usr/bin/python3 "${repo_root}/src/runners/unityhpc/jobs/cleanup_stale.py" \
        "${SLURM_JOB_ID}" "${output_dir}/logs" --apply --interval 30 \
        >>"${output_dir}/logs/apptainer-cleanup.log" 2>&1 &
    cleanup_pid=$!
fi
set +e
"${python}" -m agentic_eval.launch "${job_config}" --jobs-dir "${output_dir}/logs" --base-url "${OPENAI_BASE_URL}" --image "${image}"
status=$?
set -e
exit "${status}"
