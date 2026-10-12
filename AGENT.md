# Agent guide

## Goal

A modular framework, built on Harbor, to create, configure, and evaluate agentic frameworks on the Unity HPC cluster.
The main goals are reusability and customizable experiment configuration.

## Dir Layout

One experiment is one folder, `experiments/<name>/`.

```text
experiments/<name>/
  job.yaml                  Harbor job. Holds n_attempts and the dataset path.
  runner.yaml               Unity GPU request, wall time, and the model server.
  sandbox/sandbox.def       Apptainer recipe committed in git.
  sandbox/sandbox.sif       Built on Unity. Not committed.
  agent/agent-config.yaml   Model id, turn limit, and sampling.
  agent/prompts/system.md
  agent/skills/
  tools/verifier/           Score template. The task generator copies it into each task.
  tools/trajectory/         Writes output/<run-id>/logs/trajectory.jsonl.
  tools/eval/               Writes output/<run-id>/results/summary.json.
  tasks/<benchmark>/<task>/
    instruction.md
    task.toml
    environment/
    tests/
    solution/solve.sh
  output/<run-id>/logs/     Harbor's trial directory for that run.
  output/<run-id>/results/  Score summary for that run.

src/agentic_eval/           Commands, job checks, and the Harbor launch.
src/runners/                Shared runner library.
src/runners/unityhpc/       Unity client, task.sh, and the cluster connection.
```

The cluster address, partitions, and retry count stay in `src/runners/unityhpc/connections/config.yaml`.
`output/` is gitignored. Local tool caches live in `.cache/`, which is also gitignored.
`AGENT.md` is this guide. `AGENTS.md` and `CLAUDE.md` are symlinks to it.

## Requirements

- Build on Harbor. Use its job config and task format. Do not reimplement them.
- A new experiment is a new folder, `experiments/<name>/`, not new Python code.
- Do not hard-code hardware defaults. When you need hardware resources, use the Unity HPC controller tools to find what is available.
- Keep the repository lean. Do not add docs, agent, or memory files unless the user asks.
- Run `make check` and `make validate` before you finish a change.
