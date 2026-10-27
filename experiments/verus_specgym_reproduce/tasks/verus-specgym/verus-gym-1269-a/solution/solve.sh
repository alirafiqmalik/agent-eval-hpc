#!/bin/bash
set -euo pipefail

# Prefer Harbor's solution mount path.
if [[ -f /solution/attempted_solve.rs ]]; then
  cp /solution/attempted_solve.rs /home/dev/solve.rs
  echo "Copied /solution/attempted_solve.rs to /home/dev/solve.rs"
  exit 0
fi

# Fallback for direct docker runs where task is mounted under /task.
if [[ -f /task/solution/attempted_solve.rs ]]; then
  cp /task/solution/attempted_solve.rs /home/dev/solve.rs
  echo "Copied /task/solution/attempted_solve.rs to /home/dev/solve.rs"
  exit 0
fi

# Fallback for environments that may still retain this artifact in /home/task_specific_artifacts.
if [[ -f /home/task_specific_artifacts/attempted_solve.rs ]]; then
  cp /home/task_specific_artifacts/attempted_solve.rs /home/dev/solve.rs
  echo "Copied /home/task_specific_artifacts/attempted_solve.rs to /home/dev/solve.rs"
  exit 0
fi

# Construction-only exports may not include attempted_solve.rs. In that case,
# fall back to the editable template so the Harbor task still opens in a valid
# state.
if [[ -f /solution/solve_template.rs ]]; then
  cp /solution/solve_template.rs /home/dev/solve.rs
  echo "Copied /solution/solve_template.rs to /home/dev/solve.rs"
  exit 0
fi

if [[ -f /task/solution/solve_template.rs ]]; then
  cp /task/solution/solve_template.rs /home/dev/solve.rs
  echo "Copied /task/solution/solve_template.rs to /home/dev/solve.rs"
  exit 0
fi

if [[ -f /home/task_specific_artifacts/solve_template.rs ]]; then
  cp /home/task_specific_artifacts/solve_template.rs /home/dev/solve.rs
  echo "Copied /home/task_specific_artifacts/solve_template.rs to /home/dev/solve.rs"
  exit 0
fi

echo "Neither attempted_solve.rs nor solve_template.rs was found in /task/solution or /home/task_specific_artifacts"
exit 1
