#!/bin/bash
set -euo pipefail
mkdir -p /tmp/specgym-context
tar --no-same-owner -xzf /staging/env_files/context.tar.gz -C /tmp/specgym-context
cp -R /tmp/specgym-context/task /home/task_specific_artifacts
rm -rf /tmp/specgym-context
mkdir -p /home/dev /home/attempts /home/playground /home/problem_artifacts /workspace
cp /home/task_specific_artifacts/solve_template.rs /home/dev/solve.rs
# Harbor's standard SWE-agent selects its preexisting-repository adapter when
# /testbed exists. This also avoids its literal '$(pwd)' local-repository path.
ln -s /home/dev /testbed
git -C /home/dev init -q
# Commit an empty baseline, then stage only the public template. Disabling SWE's
# initial reset below preserves it and makes native submit produce a full patch.
test -z "$(git -C /home/dev diff --cached --name-only)"
git -C /home/dev -c core.hooksPath=/dev/null -c commit.gpgsign=false \
    -c user.name=Benchmark -c user.email=benchmark@example.invalid \
    commit --allow-empty -qm 'chore(task): initialize workspace'
git -C /home/dev add -- solve.rs
cp /home/task_specific_artifacts/problem_statement.md /home/problem_artifacts/problem_statement.md
cp -R /home/task_specific_artifacts/snippets /home/symbolic_tests
for bucket in pre_sound pre_complete post_sound post_complete; do
    mkdir -p "/home/task_specific_artifacts/snippets/${bucket}"
done
test ! -e /home/task_specific_artifacts/attempted_solve.rs
test ! -e /home/task_specific_artifacts/libsolve.rlib
test ! -e /home/task_testing_specific_artifacts/manifest.json
# The model call cap is in SWE-agent's native config: Harbor 0.24 no longer
# accepts the upstream script's per_instance_call_limit agent kwarg.
export SWEAGENT_CONFIG=/opt/specgym-swe.yaml
