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

Current state (Phase 1 vertical slice + Phase 4 exploratory notebooks only —
CI gates, scripts/, Dockerfile, pipelines/, and deploy/k8s/ from the roadmap
below do not exist yet):

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
│   └── golden_qa_de.jsonl       # only golden dataset so far (rag/summarization not yet added)
├── src/genai_eval/               # config, endpoints, datasets, metrics, eval_runner
│   └── (no compare_results.py or mlflow_logging.py module yet — MLflow logging
│        currently lives inline in the notebooks, not in src/)
├── tests/
│   └── test_offline.py          # offline unit tests, no endpoints needed
└── pyproject.toml
```

### Golden dataset schema (JSONL, one object per line)

Required: `id`, `language`, `category`, `prompt`, `max_tokens`, `temperature`. Optional eval fields: `expected_output`, `contexts` (both nullable — referenceless metrics apply when null), `metric_set` (list of metric names from the registry).

## Code Conventions

- **Env-var-first config with interactive fallback**: every runnable reads config from env vars (`MODEL_ENDPOINT`, `JUDGE_ENDPOINT`, `EMBED_ENDPOINT`, `API_KEY`, `DATASET`, `METRICS`, `OUT`), prompts interactively only when a required var is missing, and **fails fast in non-TTY contexts** (K8s Jobs have no stdin). Same pattern as the user's `run_aiperf_sustained.sh`.
- **One runner, three entry points (target design)**: local Python (`python -m genai_eval.eval_runner`), bash wrapper (`scripts/`), containerized K8s Job. Only the local Python entry point exists today; `scripts/` and the container image are not yet built. Don't create per-mode logic forks once they exist — the env-var-first design is what's meant to keep all three identical.
- **OpenShift-clean containers (target design, not yet built)**: writable `WORKDIR` on `emptyDir`, files owned by group `0` with `chmod g=u`, no fixed UID. DeepEval containers additionally need `DEEPEVAL_NO_INSPECT_PROMPT=1`, `DEEPEVAL_DISABLE_DOTENV=1`, `DEEPEVAL_RESULTS_FOLDER` set, and (optionally) `DEEPEVAL_FILE_SYSTEM=READ_ONLY`.
- **Verify APIs against pinned versions**: DeepEval, Ragas, Evidently, and `mlflow.genai` all change APIs fast. Snippets in the design doc and Phase 4 notebooks are illustrative — check the installed version's docs before relying on import paths or signatures.
- **Jupyter `.env` loading**: a notebook's `!bash`/`%%bash` cell working directory doesn't always match the notebook's location, so a relative `source .env` can fail. Use an absolute path or `source "$(pwd)/.env"` once cwd is confirmed to be the repo root (see README).

## Regression Gate Policy (three layers, evaluated in order; any failure blocks)

1. **Hard safety constraints** — zero tolerance: any PII leakage, toxicity, or policy failure on the golden set fails the build (DeepEval `assert_test` with threshold 1.0).
2. **Per-metric floors** — absolute minimums independent of baseline (calibrated per judge model).
3. **Regression checks** — candidate vs. baseline: overall delta within tolerance AND no critical-slice regression (e.g., `language=de`, per `category`). Designed to be implemented by `compare_results.py --fail-on-regression` (Phase 2, not yet built).

Suites are tiered by cost: smoke on every PR, broader nightly, full suite before major prompt/model changes. This policy is the Phase 2 target; no CI wiring exists yet.

## Common Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # then edit endpoints/model names

# Run an evaluation (flags fall back to env vars, then interactive prompt on a TTY;
# missing required values fail fast in CI/K8s)
python -m genai_eval.eval_runner \
  --dataset datasets/golden_qa_de.jsonl \
  --metrics answer_relevancy,correctness \
  --out results/qa_baseline.json

# Offline tests (no endpoints needed)
pytest tests/

# Air-gapped install
pip install --no-index --find-links=/opt/wheelhouse -r requirements.lock
```

Note: `deepeval test run` (CI smoke suite), `compare_results.py` (regression gate
layer 3), `scripts/` wrappers, and the containerized/K8s runner are Phase 2+
roadmap items — not implemented yet. See below.

## Phased Roadmap (trigger-based, not tool-accretion-based)

- **Phase 1 — Core (in progress)**: MLflow + DeepEval + direct endpoints. Done: `src/genai_eval` runner (config, endpoints, datasets, metrics, eval_runner), `golden_qa_de.jsonl`, `model_eval_deepeval.ipynb` and `model_eval_deepeval_mlflow.ipynb` PoC notebooks, offline tests. Not yet done: `golden_rag_de.jsonl` / `golden_summarization_de.jsonl`, a `compare_results.py`/`mlflow_logging.py` module in `src/` (MLflow logging currently lives inline in the notebook).
- **Phase 2 — CI gates (not started)**: three-layer policy in GitHub Actions/Tekton/K8s Jobs; `scripts/`, `Dockerfile`, `pipelines/`, `deploy/k8s/`, `tests/test_eval_smoke.py` all still to be created.
- **Phase 3 — Specialist metrics (conditional, not started)**: Ragas candidates admitted only via the acceptance rule, under an identical pinned judge configuration.
- **Phase 4 — Production (conditional; early exploration underway)**: `phase4_langfuse_prototype.ipynb` and `phase4_evidently_prototype.ipynb` exist as exploratory PoCs only — their entry-criteria (real production tracing needs / enough production history for drift windows) have **not** fired yet, so nothing from these notebooks is part of the core stack. Don't treat their presence as adoption of Langfuse/Evidently; apply the system-of-record split from day one once they graduate to real use.
- **Cross-cutting**: Prometheus/Grafana for serving infra (vLLM `/metrics`) — operational monitoring, independent of eval phases. Not started.
