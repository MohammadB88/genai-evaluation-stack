#!/usr/bin/env bash
# Thin wrapper around `python -m genai_eval.eval_runner` — the "bash" leg of
# the "one runner, three entry points" design (see CLAUDE.md). All config
# resolution (env var -> CLI flag -> TTY prompt -> fail-fast) lives in
# genai_eval.config; this script does not duplicate that logic, it only
# sources .env if present and forwards args untouched.
#
# Usage: scripts/run_eval.sh [eval_runner.py flags...]
#   scripts/run_eval.sh --dataset datasets/golden_qa_de.jsonl \
#     --metrics answer_relevancy,correctness --out results/qa.json
#
# Non-TTY contexts (CI, K8s Jobs) with missing required env vars fail fast
# with exit code 2 (see eval_runner.main); this wrapper does not swallow that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

exec python -m genai_eval.eval_runner "$@"
