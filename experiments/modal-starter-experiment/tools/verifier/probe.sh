#!/bin/bash
# Hardware probe verifier. The task generator copies one probe to /tests/probe.py.
mkdir -p /logs/verifier
if python /tests/probe.py /tests/probe-config.json >/logs/verifier/probe.json; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
