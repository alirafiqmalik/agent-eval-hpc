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
        export LD_LIBRARY_PATH="${cuda_root}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
fi

definition="${experiment_dir}/sandbox/sandbox.def"
image="${output_dir}/sandbox.sif"
# Cache by the recipe and all of its build inputs, shared by campaign shards.
definition_hash=$(cd "${experiment_dir}" && find sandbox agent -type f ! -name '*.sif' -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1)
if [[ -n "${EVAL_RUNTIME_ROOT:-}" ]]; then
    cached_image="${EVAL_RUNTIME_ROOT}/sandbox.sif"
    cached_hash="${EVAL_RUNTIME_ROOT}/sandbox.def.sha256"
    recipe_hash=$(sha256sum "${definition}" | cut -d ' ' -f 1)
    if [[ -s "${cached_image}" && -f "${cached_hash}" ]] &&
        [[ $(cat "${cached_hash}") == "${recipe_hash}" ]] &&
        ! grep -q '^%files' "${definition}"; then
        image="${cached_image}"
    fi
fi
if [[ -n "${EVAL_RUNTIME_ROOT:-}" && ! -s "${image}" ]]; then
    mkdir -p "${EVAL_RUNTIME_ROOT}/images"
    image="${EVAL_RUNTIME_ROOT}/images/${definition_hash}.sif"
    exec 9>"${image}.lock"
    flock 9
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
    rm -f -- "${image}.building"
    "${builder}" build "${image}.building" "${definition}"
    mv -- "${image}.building" "${image}"
fi
flock -u 9 2>/dev/null || true

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
export PYTHONPATH="${repo_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
"${python}" - "${experiment_dir}" "${server_script}" <<'PY'
import json
import importlib.metadata
import os
import sys
import shutil
import tempfile
from pathlib import Path

import yaml
from runners.unityhpc.jobs.local_server import select_port

experiment = Path(sys.argv[1])
agent = yaml.safe_load((experiment / "agent/agent-config.yaml").read_text())
runner = yaml.safe_load((experiment / "runner.yaml").read_text())
agent = json.loads(os.environ.get("EVAL_AGENT_CONFIG", "null")) or agent
runner = json.loads(os.environ.get("EVAL_RUNNER_CONFIG", "null")) or runner
versions = {}
for package in ("harbor", "vllm", "torch", "transformers"):
    try:
        versions[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        pass
(Path(sys.argv[2]).parent / "results/runtime.json").write_text(
    json.dumps({"packages": versions, "python": sys.version,
                "model": agent["model"], "agent_version": agent.get("version")}, indent=2) + "\n"
)
server = runner["model_server"]
mode = os.environ.get("EVAL_MODEL_SERVER_MODE", server.get("mode", "local"))
Path(str(sys.argv[2]) + ".mode").write_text(mode + "\n")
if mode != "local":
    raise SystemExit(0)
model_path = agent["model"]
if server.get("local_model_metadata", False):
    from runners.unityhpc.jobs.model_metadata import stage_metadata

    local_model = Path(tempfile.mkdtemp(prefix="agentic-sfv-model-", dir=os.environ.get("TMPDIR", "/tmp")))
    try:
        receipt = stage_metadata(Path(model_path), local_model)
        (Path(sys.argv[2]).parent / "results/model-metadata.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        Path(str(sys.argv[2]) + ".model-dir").write_text(str(local_model) + "\n")
    except BaseException:
        shutil.rmtree(local_model)
        raise
    model_path = str(local_model)
# Every Slurm allocation has its own network endpoint, even on a shared node.
port = select_port()
command = server["command"].format(
    model=model_path,
    port=port,
    context_length=server["context_length"],
)
Path(sys.argv[2]).write_text("exec " + command + "\n")
Path(str(sys.argv[2]) + ".port").write_text(str(port) + "\n")
Path(str(sys.argv[2]) + ".model").write_text(model_path + "\n")
PY
mode=$(tr -d '[:space:]' < "${server_script}.mode")

server_pid=""
harbor_pid=""
cleanup_pid=""
cleanup() {
    status=$?
    if [[ -n "${harbor_pid}" ]]; then
        kill "${harbor_pid}" >/dev/null 2>&1 || true
        wait "${harbor_pid}" 2>/dev/null || true
    fi
    if [[ "${mode}" == external ]]; then
        printf '{"exit_code":%s}\n' "${status}" >"${output_dir}/worker-exit.json"
        rm -f -- "${output_dir}/model-endpoint.env"
    fi
    if [[ -n "${cleanup_pid}" ]]; then
        kill "${cleanup_pid}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${server_pid}" ]]; then
        kill "${server_pid}" >/dev/null 2>&1 || true
        wait "${server_pid}" 2>/dev/null || true
    fi
    rm -f -- "${output_dir}/model-endpoint.env" "${output_dir}/model-endpoint.pending"
    tar -C "${output_dir}" -czf "${output_dir}/harbor-jobs.tar.gz" logs results || true
    if [[ -n "${local_image_dir}" ]]; then
        rm -rf -- "${local_image_dir}"
    fi
    if [[ -f "${server_script}.model-dir" ]]; then
        rm -rf -- "$(cat "${server_script}.model-dir")"
    fi
}
trap cleanup EXIT

launch_args=()
if [[ "${mode}" == local ]]; then
    port=$(tr -d '[:space:]' < "${server_script}.port")
    rm -f -- "${output_dir}/model-endpoint.env"
    "${python}" "${repo_root}/src/runners/unityhpc/jobs/local_server.py" \
        "${server_script}" "${port}" "$(cat "${server_script}.model")" "${output_dir}" \
        >"${output_dir}/logs/model-server.log" 2>&1 &
    server_pid=$!
    ready=0
    for _ in $(seq 1 180); do
        if ! kill -0 "${server_pid}" >/dev/null 2>&1; then
            break
        fi
        if [[ -s "${output_dir}/model-endpoint.env" ]]; then
            ready=1
            break
        fi
        sleep 10
    done
    if [[ "${ready}" != 1 ]]; then
        printf '%s\n' 'model server did not become ready' >&2
        exit 1
    fi

    source "${output_dir}/model-endpoint.env"
    launch_args+=(--base-url "${OPENAI_BASE_URL}")
elif [[ "${mode}" == external ]]; then
    "${python}" "${repo_root}/src/runners/unityhpc/jobs/remote_server.py" wait
    source "${output_dir}/model-endpoint.env"
    launch_args+=(--base-url "${OPENAI_BASE_URL}")
elif [[ "${mode}" != none ]]; then
    printf '%s\n' "Unknown model server mode: ${mode}" >&2
    exit 1
fi
export PYTHONPATH="${repo_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    /usr/bin/python3 "${repo_root}/src/runners/unityhpc/jobs/cleanup_stale.py" \
        "${SLURM_JOB_ID}" "${output_dir}/logs" --apply --interval 30 \
        >>"${output_dir}/logs/apptainer-cleanup.log" 2>&1 &
    cleanup_pid=$!
fi
set +e
if [[ "${mode}" == local ]]; then
    "${python}" -m agentic_eval.launch "${job_config}" --jobs-dir "${output_dir}/logs" "${launch_args[@]}" --image "${image}" &
    harbor_pid=$!
    wait -n -p finished_pid "${server_pid}" "${harbor_pid}"
    status=$?
    if [[ "${finished_pid:-}" != "${harbor_pid}" ]]; then
        printf '%s\n' 'owned model server stopped during evaluation' >&2
        kill "${harbor_pid}" 2>/dev/null || true
        wait "${harbor_pid}" 2>/dev/null || true
        status=1
    fi
    harbor_pid=""
else
    "${python}" -m agentic_eval.launch "${job_config}" --jobs-dir "${output_dir}/logs" "${launch_args[@]}" --image "${image}"
    status=$?
fi
set -e
exit "${status}"
