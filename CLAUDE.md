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

```
├── genai-evaluation-stack-design.md   # full design doc — source of truth for decisions
├── .env.example                # all env vars (endpoints, telemetry opt-outs, HF offline)
├── docs/                       # concepts, tool comparison, air-gap setup, judge endpoints,
│                               # regression policy, metric-registry.md
├── notebooks/                  # numbered PoC notebooks (DeepEval judge, RAG eval,
│                               # summarization, MLflow tracking, [Phase 4] Evidently drift)
├── datasets/                   # golden_qa_de.jsonl, golden_rag_de.jsonl, golden_summarization_de.jsonl
├── src/genai_eval/             # config, endpoints, datasets, metrics, eval_runner,
│                               # compare_results, mlflow_logging
├── scripts/                    # run_eval.sh, run_regression_gate.sh (bash wrappers)
├── Dockerfile                  # containerized runner image for K8s Jobs
├── pipelines/                  # github-actions/, tekton/, k8s/ (eval-job.yaml)
├── deploy/k8s/                 # per-component manifests (mlflow core; langfuse/evidently/
│                               # litellm/otel-collector/prom-grafana conditional)
└── tests/                      # test_eval_smoke.py — deepeval smoke suite for CI
```

### Golden dataset schema (JSONL, one object per line)

Required: `id`, `language`, `category`, `prompt`, `max_tokens`, `temperature`. Optional eval fields: `expected_output`, `contexts` (both nullable — referenceless metrics apply when null), `metric_set` (list of metric names from the registry).

## Code Conventions

- **Env-var-first config with interactive fallback**: every runnable reads config from env vars (`MODEL_ENDPOINT`, `JUDGE_ENDPOINT`, `EMBED_ENDPOINT`, `API_KEY`, `DATASET`, `METRICS`, `OUT`), prompts interactively only when a required var is missing, and **fails fast in non-TTY contexts** (K8s Jobs have no stdin). Same pattern as the user's `run_aiperf_sustained.sh`.
- **One runner, three entry points**: local Python (`python -m genai_eval.eval_runner`), bash wrapper (`scripts/`), containerized K8s Job. The exit code carries the gate result (failed Job = failed gate). Don't create per-mode logic forks — the env-var-first design is what keeps all three identical.
- **OpenShift-clean containers**: writable `WORKDIR` on `emptyDir`, files owned by group `0` with `chmod g=u`, no fixed UID. DeepEval containers additionally need `DEEPEVAL_NO_INSPECT_PROMPT=1`, `DEEPEVAL_DISABLE_DOTENV=1`, `DEEPEVAL_RESULTS_FOLDER` set, and (optionally) `DEEPEVAL_FILE_SYSTEM=READ_ONLY`.
- **Verify APIs against pinned versions**: DeepEval, Ragas, Evidently, and `mlflow.genai` all change APIs fast. Snippets in the design doc are illustrative — check the installed version's docs before relying on import paths or signatures.

## Regression Gate Policy (three layers, evaluated in order; any failure blocks)

1. **Hard safety constraints** — zero tolerance: any PII leakage, toxicity, or policy failure on the golden set fails the build (DeepEval `assert_test` with threshold 1.0).
2. **Per-metric floors** — absolute minimums independent of baseline (calibrated per judge model).
3. **Regression checks** — candidate vs. baseline: overall delta within tolerance AND no critical-slice regression (e.g., `language=de`, per `category`). Implemented by `compare_results.py --fail-on-regression`.

Suites are tiered by cost: smoke on every PR, broader nightly, full suite before major prompt/model changes.

## Common Commands

```bash
# Run DeepEval smoke suite (CI layers 1+2; non-zero exit on threshold fail)
deepeval test run tests/test_eval_smoke.py

# Run an evaluation
python -m genai_eval.eval_runner \
  --dataset datasets/golden_rag_de.jsonl \
  --model-endpoint http://vllm-app:8000/v1 \
  --judge-endpoint http://vllm-judge:8000/v1 \
  --metrics faithfulness,answer_relevancy,context_recall \
  --out results/rag_v3.json

# Compare against baseline (CI layer 3)
python -m genai_eval.compare_results --baseline results/main.json --candidate results/pr.json --fail-on-regression

# Air-gapped install
pip install --no-index --find-links=/opt/wheelhouse -r requirements.lock
```

## Phased Roadmap (trigger-based, not tool-accretion-based)

- **Phase 1 — Core**: MLflow + DeepEval + direct endpoints; notebooks, golden datasets, CLI runner; everything logged to MLflow.
- **Phase 2 — CI gates**: three-layer policy in GitHub Actions/Tekton/K8s Jobs.
- **Phase 3 — Specialist metrics (conditional)**: Ragas candidates admitted only via the acceptance rule, under an identical pinned judge configuration.
- **Phase 4 — Production (conditional)**: Langfuse + Evidently + OTel Collector trace routing (CI/dev → MLflow, prod → Langfuse); apply the system-of-record split from day one.
- **Cross-cutting**: Prometheus/Grafana for serving infra (vLLM `/metrics`) — operational monitoring, independent of eval phases.
