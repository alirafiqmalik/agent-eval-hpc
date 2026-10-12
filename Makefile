.DEFAULT_GOAL := help

UV ?= $(if $(wildcard .cache/setup-tools/bin/uv),.cache/setup-tools/bin/uv,uv)
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export UV_PYTHON_INSTALL_DIR ?= $(CURDIR)/.cache/python
EXPERIMENT ?= experiments/unityhpc-starter-experiment/job.yaml
EXPERIMENT_DIR := $(patsubst %/,%,$(dir $(EXPERIMENT)))
RUN_ID ?=
WALLTIME_SECONDS ?=
RUNNER_CONFIG ?= src/runners/unityhpc/connections/config.yaml

EVAL = $(UV) run --locked agentic-eval

.PHONY: help setup check validate run require-run submit status fetch evaluate collect

help:
	@printf '%s\n' \
	  'make setup                       Install locked dependencies, including Harbor' \
	  'make check                       Run tests and lint checks' \
	  'make validate                    Check EXPERIMENT and its Harbor tasks' \
	  'make run RUN_ID=<id>             Run EXPERIMENT locally with harbor run' \
	  'make submit RUN_ID=<id>          Submit EXPERIMENT to Unity' \
	  'make status RUN_ID=<id>          Read Unity status' \
	  'make fetch RUN_ID=<id>           Download the run into the experiment output' \
	  'make evaluate RUN_ID=<id>        Submit, wait, fetch, and verify a Unity run' \
	  'make collect RUN_ID=<id>         Resume collection for an existing Unity run' \
	  'Starter rerun: make evaluate RUN_ID=<new-id> RUNNER_CONFIG=src/runners/unityhpc/connections/short.yaml WALLTIME_SECONDS=10800' \
	  '' \
	  'Set EXPERIMENT to another job.yaml. Set a new RUN_ID for each submission.' \
	  'Set RUNNER_CONFIG to select a Unity connection.' \
	  'Other Unity accounts can set UNITYHPC_CONTROLLER_URL and UNITYHPC_RUNTIME_ROOT.'

setup:
	./scripts/setup.sh

check:
	$(UV) run --locked pytest
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .

validate:
	$(EVAL) validate "$(EXPERIMENT)"

run: require-run
	$(EVAL) run "$(EXPERIMENT)" --run-id "$(RUN_ID)"

require-run:
	@test -n "$(RUN_ID)" || { printf '%s\n' 'Set RUN_ID to a new run identity, for example RUN_ID=starter-001.' >&2; exit 2; }

submit: require-run
	$(EVAL) submit "$(EXPERIMENT)" --run-id "$(RUN_ID)" --runner-config "$(RUNNER_CONFIG)" $(if $(WALLTIME_SECONDS),--walltime-seconds "$(WALLTIME_SECONDS)")

status: require-run
	$(EVAL) status --run-id "$(RUN_ID)" --runner-config "$(RUNNER_CONFIG)"

fetch: require-run
	$(EVAL) fetch --run-id "$(RUN_ID)" --runner-config "$(RUNNER_CONFIG)" --output "$(EXPERIMENT_DIR)/output/$(RUN_ID)"

evaluate: require-run
	./scripts/run_unity_eval.sh "$(EXPERIMENT)" --run-id "$(RUN_ID)" --runner-config "$(RUNNER_CONFIG)" $(if $(WALLTIME_SECONDS),--walltime-seconds "$(WALLTIME_SECONDS)")

collect: require-run
	./scripts/collect_unity_eval.sh "$(EXPERIMENT)" --run-id "$(RUN_ID)" --runner-config "$(RUNNER_CONFIG)"
