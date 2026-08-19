# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A self-hosted, air-gap-friendly evaluation stack for GenAI/LLM applications: offline metric evaluation (RAG faithfulness, relevancy, correctness), experiment tracking, CI regression gates against golden datasets, and (later) production monitoring. It is the **quality axis** complementary to the user's existing AIPerf/k6 **performance axis** — both log to MLflow so each model/prompt candidate carries a joint quality-vs-performance record.

The full design rationale, tool comparison, and roadmap live in `genai-evaluation-stack-design.md`. Read it before making architectural changes; this file summarizes its binding decisions.

## Architecture Decisions (binding)

### Core stack — Phase 1 (nothing else by default)

| Layer | Tool |
|---|---|
| Metrics & CI assertions | **DeepEval** (pytest-style `assert_test`, G-Eval/DAG, native RAG metrics) |
| Experiments, artifacts, prompt versions, initial tracing | **MLflow 3.x** (self-hosted, `mlflow.genai.evaluate`, prompt registry) |
| Model + judge access | **Direct OpenAI-compatible endpoints** (`base_url` + `api_key`, self-hosted vLLM) |

### Trigger-based additions — do NOT add these unless their entry criterion has fired

| Tool | Add only when |
|---|---|
| **LiteLLM proxy** | Routing/budgets/virtual keys across many endpoints are actually needed |
| **Ragas** | A specific metric/dataset-generator passes the acceptance rule (see below) |
| **Langfuse** | Production tracing, annotation workflows, prompt release management become real requirements (Phase 4) |
| **Evidently** | Enough production history exists to define drift reference/current windows (Phase 4) |

### Non-negotiable rules

- **One metric name → one owning framework**, recorded in `docs/metric-registry.md`. Never run two frameworks' implementations of the "same" metric in parallel — a 0.82 "faithfulness" in one framework is not comparable to 0.82 in another. A second framework's metric is adopted only after (a) measuring agreement with human labels on the golden set and (b) demonstrating added value over the corresponding DeepEval/MLflow scorer.
- **Pinned judge configuration**: the judge model **and** judge prompt version are part of every baseline. Log `judge_model` + `judge_prompt_ver` with every MLflow run. A judge change invalidates baselines and floors — it requires re-scoring the baseline and recalibrating thresholds. Judge `temperature=0`.
- **System of record**: offline/pre-release truth (experiments, golden datasets, CI runs, release evidence, prompt development versions) lives in **MLflow**. Online/post-release truth (sessions, live traces, feedback, annotation, prompt release labels) lives in **Langfuse** once it exists. A score, prompt version, or trace has exactly one authoritative home. Prometheus (if deployed) holds only low-cardinality numeric aggregates — never prompt/response content, never the score of record.
- **Air-gap by default**: all tools must be muzzled — `DEEPEVAL_TELEMETRY_OPT_OUT=1`, `RAGAS_DO_NOT_TRACK=true` (if adopted), `PHOENIX_TELEMETRY_ENABLED=false` (if used), `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. Prefer LLM-judge metrics (only need the local endpoint) over metrics requiring HF model downloads. Pin exact dependency versions (`requirements.lock`) and support wheelhouse installs (`pip install --no-index --find-links=...`).
- **Golden datasets are code**: version-controlled JSONL, changed only via reviewed PR, never silently edited (the regression gate must not compare against a moving target). Keep `language`/`category` metadata for score slicing.

## Repository Structure

Current state (Phase 1 done + Phase 2 in progress + Phase 4 exploratory
notebooks only — CI wiring (pipelines/, deploy/k8s/) from the roadmap below
does not exist yet):

```
├── genai-evaluation-stack-design.md     # full design doc — source of truth for decisions
├── README.md                    # quickstart, endpoint sanity checks, env vars
├── .env.example                 # all env vars (endpoints, telemetry opt-outs, HF offline)
├── docs/
│   └── metric-registry.md       # one metric name → one owning framework
├── notebooks/
│   ├── model_eval_deepeval.ipynb        # DeepEval LLM-as-judge PoC against golden_qa_de
│   ├── model_eval_deepeval_mlflow.ipynb # same, + logging runs/scores to MLflow
│   ├── phase4_langfuse_prototype.ipynb  # exploratory only — Phase 4 trigger not fired
│   ├── phase4_evidently_prototype.ipynb # exploratory only — Phase 4 trigger not fired
│   ├── requirements.txt         # notebook-only deps, split from src/ requirements
│   └── sample-prompts/content_generation.jsonl
├── datasets/
│   ├── golden_qa_de.jsonl               # QA (+ a few inline rag-category items)
│   ├── golden_rag_de.jsonl              # dedicated RAG set: faithfulness, contextual_precision/recall
│   └── golden_summarization_de.jsonl    # summarization + toxicity (referenceless)
├── src/genai_eval/               # config, endpoints, datasets, metrics, eval_runner,
│   │                              # mlflow_logging, compare_results
│   └── (no K8s Job / pipelines/ yet invoke compare_results.py in CI)
├── scripts/
│   ├── run_eval.sh               # bash wrapper around eval_runner.py — sources .env, forwards args
│   └── run_gate.sh               # bash wrapper around compare_results.py — same convention
├── tests/
│   ├── test_offline.py          # offline unit tests, no endpoints needed
│   ├── test_compare_results.py  # offline regression-gate logic tests
│   └── test_eval_smoke.py       # live-endpoint smoke suite; skipped without MODEL_ENDPOINT/JUDGE_ENDPOINT
├── Dockerfile                    # OpenShift-clean image wrapping eval_runner (not yet build-verified)
└── pyproject.toml
```

### Golden dataset schema (JSONL, one object per line)

Required: `id`, `language`, `category`, `prompt`, `max_tokens`, `temperature`. Optional eval fields: `expected_output`, `contexts` (both nullable — referenceless metrics apply when null), `metric_set` (list of metric names from the registry).

## Code Conventions

- **Env-var-first config with interactive fallback**: every runnable reads config from env vars (`MODEL_ENDPOINT`, `JUDGE_ENDPOINT`, `EMBED_ENDPOINT`, `API_KEY`, `DATASET`, `METRICS`, `OUT`), prompts interactively only when a required var is missing, and **fails fast in non-TTY contexts** (K8s Jobs have no stdin). Same pattern as the user's `run_aiperf_sustained.sh`.
- **One runner, three entry points**: local Python (`python -m genai_eval.eval_runner`), bash wrapper (`scripts/run_eval.sh`, `scripts/run_gate.sh`), containerized K8s Job (`Dockerfile`, K8s Job manifest still to be written). All three now exist except the K8s Job manifest itself. The bash wrappers are intentionally thin — they source `.env` if present and forward args untouched; no logic forks live in bash, config resolution stays entirely in `genai_eval.config`.
- **OpenShift-clean containers**: writable `WORKDIR` on `emptyDir`, files owned by group `0` with `chmod g=u`, no fixed UID. DeepEval containers additionally need `DEEPEVAL_NO_INSPECT_PROMPT=1`, `DEEPEVAL_DISABLE_DOTENV=1`, `DEEPEVAL_RESULTS_FOLDER` set, and (optionally) `DEEPEVAL_FILE_SYSTEM=READ_ONLY`. Implemented in `Dockerfile` — not yet build-verified (no Docker in this dev environment).
- **Verify APIs against pinned versions**: DeepEval, Ragas, Evidently, and `mlflow.genai` all change APIs fast. Snippets in the design doc and Phase 4 notebooks are illustrative — check the installed version's docs before relying on import paths or signatures.
- **Jupyter `.env` loading**: a notebook's `!bash`/`%%bash` cell working directory doesn't always match the notebook's location, so a relative `source .env` can fail. Use an absolute path or `source "$(pwd)/.env"` once cwd is confirmed to be the repo root (see README).

## Regression Gate Policy (three layers, evaluated in order; any failure blocks)

1. **Hard safety constraints** — zero tolerance: any PII leakage, toxicity, or policy failure on the golden set fails the build (DeepEval `assert_test` with threshold 1.0). Implemented in `compare_results.py::check_hard_safety` — every item must pass every `--safety-metrics` metric (default: `toxicity`), independent of baseline.
2. **Per-metric floors** — absolute minimums independent of baseline (calibrated per judge model). Implemented in `compare_results.py::check_metric_floors` against each metric's `MetricSpec.threshold`; direction-aware via `MetricSpec.higher_is_better` (e.g. `toxicity` threshold is a ceiling, not a floor).
3. **Regression checks** — candidate vs. baseline: overall delta within `--tolerance` AND no critical-slice regression (`language`, then `category`) beyond `--slice-tolerance`. Implemented in `compare_results.py::check_regression`.

`compare_results.py` is implemented (`python -m genai_eval.compare_results --candidate ... --baseline-file ... --fail-on-regression`). Baseline resolution tries MLflow first (`--mlflow-tracking-uri` / `MLFLOW_TRACKING_URI` env, `--baseline-run-id` or a run tagged `baseline=true`; bounded by a 5s/1-retry timeout so an unreachable server degrades instead of hanging), then falls back to `--baseline-file` (a static results JSON). Not yet wired: GitHub Actions/Tekton/K8s Job invocation of this gate.

Suites are tiered by cost: smoke on every PR (`tests/test_eval_smoke.py`, requires live `MODEL_ENDPOINT`/`JUDGE_ENDPOINT`, self-skips otherwise), broader nightly, full suite before major prompt/model changes. CI wiring (the workflow that actually invokes these tiers) does not exist yet.

## Common Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # then edit endpoints/model names

# Run an evaluation — local Python, or the equivalent bash wrapper
# (flags fall back to env vars, then interactive prompt on a TTY;
# missing required values fail fast in CI/K8s)
python -m genai_eval.eval_runner \
  --dataset datasets/golden_qa_de.jsonl \
  --metrics answer_relevancy,correctness \
  --out results/qa_baseline.json
# scripts/run_eval.sh --dataset datasets/golden_qa_de.jsonl \
#   --metrics answer_relevancy,correctness --out results/qa_baseline.json

# Offline tests (no endpoints needed; set DEEPEVAL_TELEMETRY_OPT_OUT=1 or
# imports may hang trying to phone home)
DEEPEVAL_TELEMETRY_OPT_OUT=1 pytest tests/

# Regression gate: candidate vs. baseline (file baseline shown; MLflow baseline
# via --mlflow-tracking-uri / MLFLOW_TRACKING_URI takes precedence when set)
python -m genai_eval.compare_results \
  --candidate results/qa_candidate.json \
  --baseline-file results/qa_baseline.json \
  --fail-on-regression
# scripts/run_gate.sh --candidate results/qa_candidate.json \
#   --baseline-file results/qa_baseline.json --fail-on-regression

# Container build (OpenShift-clean; no Docker available to verify in this environment)
docker build -t genai-eval .
docker run --rm -v "$(pwd)/results:/workspace/results" \
  --env-file .env genai-eval \
  --dataset datasets/golden_qa_de.jsonl --metrics answer_relevancy --out results/eval.json

# Air-gapped install
pip install --no-index --find-links=/opt/wheelhouse -r requirements.lock
```

Note: the K8s Job manifest (`deploy/k8s/`) and CI pipeline wiring
(`pipelines/`) are still Phase 2 roadmap items — not implemented yet.
MLflow logging (`mlflow_logging.py`), the regression gate
(`compare_results.py`), and the bash wrappers (`scripts/`) are implemented.
The `Dockerfile` exists but has not been build-verified (no Docker in this
dev environment) — verify with `docker build` before relying on it in
CI/OpenShift. See below.

## Phased Roadmap (trigger-based, not tool-accretion-based)

- **Phase 1 — Core (done)**: MLflow + DeepEval + direct endpoints. `src/genai_eval` runner (config, endpoints, datasets, metrics, eval_runner, mlflow_logging — wired into the CLI), `golden_qa_de.jsonl` / `golden_rag_de.jsonl` / `golden_summarization_de.jsonl`, full metric registry (`answer_relevancy`, `correctness`, `faithfulness`, `contextual_precision`, `contextual_recall`, `summarization`, `toxicity` — all DeepEval, no backlog remaining), `model_eval_deepeval.ipynb` and `model_eval_deepeval_mlflow.ipynb` PoC notebooks, offline tests.
- **Phase 2 — CI gates (in progress)**: Done: `compare_results.py` (regression gate layer 3, MLflow-baseline-with-static-JSON-fallback), `tests/test_compare_results.py` (offline gate-logic tests), `tests/test_eval_smoke.py` (live-endpoint smoke tier, self-skips without endpoints), `Dockerfile` + `.dockerignore` (OpenShift-clean, not yet build-verified), `scripts/run_eval.sh` + `scripts/run_gate.sh` (bash wrappers, second of the three entry points). Not yet done: `pipelines/` + `deploy/k8s/` (CI/K8s Job wiring that actually invokes the gate — the third entry point), Docker build verification.
- **Phase 3 — Specialist metrics (conditional, not started)**: Ragas candidates admitted only via the acceptance rule, under an identical pinned judge configuration.
- **Phase 4 — Production (conditional; early exploration underway)**: `phase4_langfuse_prototype.ipynb` and `phase4_evidently_prototype.ipynb` exist as exploratory PoCs only — their entry-criteria (real production tracing needs / enough production history for drift windows) have **not** fired yet, so nothing from these notebooks is part of the core stack. Don't treat their presence as adoption of Langfuse/Evidently; apply the system-of-record split from day one once they graduate to real use.
- **Cross-cutting**: Prometheus/Grafana for serving infra (vLLM `/metrics`) — operational monitoring, independent of eval phases. Not started.
