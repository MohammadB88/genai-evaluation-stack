#!/usr/bin/env bash
# Thin wrapper around `python -m genai_eval.compare_results` — the regression
# gate (Phase 2, layer 3). Same convention as run_eval.sh: sources .env if
# present, forwards all args untouched, no logic duplicated here.
#
# Usage: scripts/run_gate.sh [compare_results.py flags...]
#   scripts/run_gate.sh --candidate results/qa_candidate.json \
#     --baseline-file results/qa_baseline.json --fail-on-regression
#
# Exit code is the gate's own: 0 = passed (or --fail-on-regression not set),
# 1 = gate failed with --fail-on-regression set.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

exec python -m genai_eval.compare_results "$@"
