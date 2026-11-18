#!/bin/bash
set -euo pipefail

mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt

python3 /tests/grade.py
