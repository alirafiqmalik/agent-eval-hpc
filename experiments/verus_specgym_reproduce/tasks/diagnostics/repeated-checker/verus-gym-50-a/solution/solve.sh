#!/bin/bash
set -euo pipefail
cp /solution/attempted_solve.rs /home/dev/solve.rs
verus_gym_specgen_check > /logs/agent/checker-first.txt 2>&1
sha256sum /logs/agent/compressed_verdicts/1/results.zip > /logs/agent/first-check.sha256
rm -r /home/attempts
verus_gym_specgen_check > /logs/agent/checker-second.txt 2>&1
test -s /logs/agent/compressed_verdicts/2/results.zip
sha256sum -c /logs/agent/first-check.sha256
