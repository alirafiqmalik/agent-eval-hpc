#!/usr/bin/env bash
# Install the local project without allocating cluster resources.
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"
export UV_CACHE_DIR=${UV_CACHE_DIR:-"$project_root/.cache/uv"}
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-"$project_root/.cache/python"}

uv_version=0.11.2
python_version=$(tr -d '[:space:]' < .python-version)

usage() {
    cat <<'EOF'
Usage: scripts/setup.sh

Install Python and the locked project dependencies, including Harbor.
No sandbox daemon or cluster runtime is installed.
EOF
}

for argument in "$@"; do
    case "$argument" in
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown setup option: %s\n' "$argument" >&2; usage >&2; exit 2 ;;
    esac
done

is_pinned_uv() {
    local version
    version=$("$1" --version 2>/dev/null) || return 1
    [[ "$version" == "uv $uv_version" || "$version" == "uv $uv_version "* ]]
}

uv_bin=${UV:-}
if [[ -n "$uv_bin" ]]; then
    if ! is_pinned_uv "$uv_bin"; then
        printf 'UV must select uv %s. Received: %s\n' "$uv_version" "$uv_bin" >&2
        exit 1
    fi
else
    installed_uv=$(command -v uv || true)
    bootstrap_root="$project_root/.cache/setup-tools"
    if [[ -n "$installed_uv" ]] && is_pinned_uv "$installed_uv"; then
        uv_bin=$installed_uv
    elif [[ -x "$bootstrap_root/bin/uv" ]] && is_pinned_uv "$bootstrap_root/bin/uv"; then
        uv_bin="$bootstrap_root/bin/uv"
    else
        if ! command -v python3 >/dev/null 2>&1; then
            printf 'Install Python 3 with venv support, or install uv %s on PATH.\n' "$uv_version" >&2
            exit 1
        fi
        python3 -m venv "$bootstrap_root"
        "$bootstrap_root/bin/python" -m pip install --disable-pip-version-check "uv==$uv_version"
        uv_bin="$bootstrap_root/bin/uv"
        if ! is_pinned_uv "$uv_bin"; then
            printf 'The local uv installation does not match version %s.\n' "$uv_version" >&2
            exit 1
        fi
    fi
fi

sync_arguments=(--locked --python "$python_version" --group dev)
"$uv_bin" sync "${sync_arguments[@]}"
"$uv_bin" run "${sync_arguments[@]}" python -c '
import importlib.metadata
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit("The project requires Python 3.12.")
print("Installed agentic-sfv-framework " + importlib.metadata.version("agentic-sfv-framework"))
'
printf 'Local setup complete. Run make check and make validate.\n'
