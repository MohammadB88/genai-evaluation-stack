# Open-Source GenAI/LLM Evaluation Stack — Design & Blueprint for `genai-evaluation-stack`

## TL;DR
- **Lean core stack (Phase 1): MLflow 3.x + DeepEval + your OpenAI-compatible model/judge endpoints — nothing else.** MLflow covers experiment history, artifacts, prompt versions, and initial tracing; DeepEval covers pytest-style assertions and application-specific metrics. All other tools (Ragas, Langfuse, Evidently, LiteLLM) are **trigger-based additions**, each with an explicit entry criterion — not defaults.
- **One metric schema.** Ragas is a *specialist plug-in*, not core: adopt a Ragas metric only after measuring its agreement with human labels and demonstrating added value over the corresponding DeepEval/MLflow scorer. Never run two frameworks' versions of the "same" metric in parallel — a 0.82 "faithfulness" in one framework is not comparable to 0.82 in another.
- **Explicit ownership split** between MLflow (offline: experiments, datasets, CI runs, release evidence) and Langfuse (online: observability, sessions, feedback, annotation) — so there is never ambiguity about where the authoritative score, prompt version, or trace lives.
- **Every metric/tracking tool phones home by default and must be muzzled:** `DEEPEVAL_TELEMETRY_OPT_OUT=1`, `RAGAS_DO_NOT_TRACK=true`, `PHOENIX_TELEMETRY_ENABLED=false` (if Phoenix is used), plus `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` for local embedding/NLI/statistical-metric models. MLflow OSS makes no vendor callbacks; Langfuse core is MIT and runs air-gapped.
- **This stack is the quality axis complementary to your AIPerf/k6 performance axis:** AIPerf measures latency/throughput/tokens-per-second; this stack measures faithfulness, correctness, relevancy, and regression against golden datasets — wired into CI so prompt/model swaps fail the build before shipping.

---

## A) CONCEPT SECTION — The "What & Why" of GenAI Evaluation

### Why evaluate LLM outputs at all
Unlike deterministic software, LLM systems fail silently and non-reproducibly. The failure modes that make evaluation mandatory:
- **Hallucination**: fabricated facts not grounded in the source/context.
- **Regressions from prompt edits**: fixing one edge case silently breaks others across the input distribution.
- **Model swaps / vendor snapshot drift**: a provider ships a new checkpoint under the same name, or you migrate from model A to model B, and quality shifts invisibly.
- **Retrieval degradation** (RAG): the retriever starts pulling irrelevant chunks.
- **Compliance & safety**: toxicity, PII leakage, policy violations, tone.

Without automated evals wired into CI, these are discovered from angry users rather than a failing build.

### Types of evaluation
| Type | What it does | Strengths | Limits |
|---|---|---|---|
| **Statistical / classic NLP** (BLEU, ROUGE, METEOR, BERTScore) | Compare output to a reference string by n-gram overlap or embedding similarity | Cheap, deterministic, fast, no LLM needed | Poor correlation with human judgment for open-ended generation; penalize valid paraphrases; BLEU/ROUGE are surface-level; BERTScore needs a local model |
| **Reference-based** | Compares `actual_output` to an `expected_output`/ground truth | Objective when ground truth exists | Requires labeled golden data; not usable on live traffic |
| **Reference-free (referenceless)** | Judges output using only input (+ retrieved context) | Works on live production traffic without labels | Usually LLM-as-judge, so inherits judge bias/variance |
| **LLM-as-judge** (G-Eval, QAG, DAG) | An LLM scores/classifies another LLM's output against criteria | Flexible, human-like on subjective criteria, gives reasons | Cost/latency, judge bias, score variance; needs calibration against humans |
| **Human-in-the-loop** | Domain experts annotate/label | Gold standard; calibrates automated judges | Slow, expensive, not scalable |

**Key principle**: no single metric suffices. Combine 2–3 generic metrics per system type + 1–2 custom G-Eval criteria, and cross-check the judge against a small set of human labels.

### The RAG triad
RAG failures can originate in retrieval or generation, so evaluate each leg:
- **Faithfulness / groundedness**: is the answer supported by the retrieved context? (catches hallucination)
- **Answer relevancy**: does the answer address the user's question? (generator quality; referenceless)
- **Context relevance / precision / recall**: did the retriever fetch the right chunks? (retriever quality)

### Offline/batch evaluation vs online monitoring
- **Offline/batch (development)**: run a fixed golden dataset through the pipeline, score with metrics, compare to a baseline. This is where CI regression gates live.
- **Online monitoring (production)**: trace live requests, run *referenceless* metrics (faithfulness, relevancy, toxicity) on sampled traffic, and watch for **drift** — distribution shifts in inputs (new topics/languages), outputs (length, sentiment, refusal rate), or quality scores over time.

### Drift monitoring for LLM apps
Classic ML drift detection (PSI, Kolmogorov–Smirnov, embeddings drift) applies to text via **descriptors**: per-text computed values (length, sentiment, toxicity, semantic similarity, custom LLM-judge scores). You compare a reference window to the current window and alert on shift.

### Evaluation in CI/CD
The maturity endpoint is treating prompts/models like code: a **golden dataset** of representative inputs (with expected outputs where available), a suite of metric assertions with thresholds, and a **CI gate** that fails the build (or blocks the merge) when a metric drops below threshold. Split suites by cost: fast smoke tests on every PR, broader suites nightly, full suite before major prompt/model changes. The gate itself follows the three-layer regression policy defined in Section C.

---

## B) TOOL COMPARISON — The "How, Part 1"

### B.1 Evaluation frameworks

| Criterion | **DeepEval** (Confident AI) | **Ragas** (exploding gradients / vibrantlabs) | **Giskard** |
|---|---|---|---|
| License | Apache-2.0 | Apache-2.0 | Apache-2.0 |
| Current state (mid-2026) | v4.0 line; latest tag **v4.0.5 released 28 May 2026** (GitHub Releases), announced as an "Eval Harness for Coding Agents" with 1-line integrations and a TUI for trace inspection; **16.7k GitHub stars**; very active | v0.3/v0.4 line; **12.6k GitHub stars**; slower cadence (repo last pushed early 2026) | v3 is a beta rewrite (Python 3.12+); RAGET/Scan still rely on v2 (no longer actively maintained) |
| Focus | Pytest-style unit testing of LLM apps; **50+ ready-to-use metrics** (per DeepEval's "Introducing DeepEval 4.0" blog, used by teams at companies including Google, Uber and LEGO) | RAG-centric metrics + synthetic test-set generation | Vulnerability scanning + RAGET (RAG eval toolkit) + red-teaming |
| RAG metrics | Native Faithfulness, Answer Relevancy, Contextual Precision/Recall/Relevancy (JSON-confineable, give reasons) | Faithfulness, LLMContextRecall, ContextPrecision, FactualCorrectness, AnswerRelevancy, SemanticSimilarity | RAGET scores generator, retriever, rewriter, routing, knowledge base separately; auto-generates test sets from a knowledge base |
| General gen-quality metrics | G-Eval (custom criteria), DAG (deterministic decision-tree), Summarization, Toxicity, Bias, Hallucination, TaskCompletion | AspectCritic (coherence/harmfulness/correctness), custom DiscreteMetric | Scan detects hallucination, harmfulness, prompt injection, robustness, bias |
| Custom/G-Eval-style | G-Eval + DAGMetric; override prompt templates per metric | DiscreteMetric / custom metric classes | Checks (v3), custom tests |
| LLM-as-judge via custom OpenAI-compatible endpoint | Yes — `LocalModel(base_url=..., api_key=...)`, `deepeval set-local-model`, or `OpenAIModel`/`GPTModel`; also `LiteLLMModel(base_url=...)` | Yes — `llm_factory` with an `AsyncOpenAI(base_url=...)` client, or `LangchainLLMWrapper(ChatOpenAI(base_url=...))`; embeddings likewise | Yes — via a model wrapper function; supports OpenAI, Azure, local via Ollama, any API |
| CI integration | First-class (`deepeval test run`, Pytest, `assert_test`, exit codes) | Usable in CI; returns scores | CI-friendly scans/test suites |
| Air-gap suitability | Good, with care (see §D); LLM-judge metrics need no local model; BERTScore/ROUGE/older Hallucination metric need pre-cached HF models | Good; wrap local LLM + local embeddings; disable tracking | Good; Apache lib runs locally; telemetry opt-out available |
| Phones home | PostHog + Sentry (opt-in error reporting); historically also `blocked_by_firewall()`→google:80 and `api.ipify.org` | Anonymized usage analytics (PostHog-style) | Aggregated usage analytics (opt-out available) |
| Disable telemetry | `DEEPEVAL_TELEMETRY_OPT_OUT=1` (see §D — value nuance across versions) | `RAGAS_DO_NOT_TRACK=true` | Documented opt-out env var |

**Verdict**: **DeepEval is the single core framework** — breadth of metrics, Pytest/CI ergonomics, cleanest custom-judge story, and JSON-confinement for weaker local judges. It already provides the major RAG metrics natively. **Ragas is a specialist plug-in, not core**: running both by default would create two metric schemas, two judge-prompt implementations, two sets of parser failures, and differently scaled scores sharing the same name ("faithfulness" 0.82 ≠ 0.82 across frameworks). Adopt a Ragas metric (or its synthetic test-set generation) **only** after it passes the acceptance rule in the Recommended Stack section. Keep **Giskard optional** for red-teaming/vulnerability scanning and its RAGET component-attribution when you need to know *which* RAG stage failed. Note DeepEval can even wrap Ragas metrics (`deepeval.metrics.ragas`), but its own native RAG metrics are preferred (debuggable, avoid Ragas NaN-on-invalid-JSON).

**Briefly on the alternatives you named:** *promptfoo* (MIT, now part of OpenAI but remains open source) is an excellent declarative CLI for prompt/RAG regression testing with a first-class GitHub Action — strong for CI gates, though its team noted it is not built for fully air-gapped use (combine local providers with egress controls or use its On-Prem edition). *OpenAI Evals* and *lm-evaluation-harness* target benchmark-style/academic evals rather than app-quality RAG/summarization. *LangSmith* evaluators are SaaS/enterprise-self-host and thus disqualified for a strict air-gap. None displace the three named frameworks for your use cases.

### B.2 Experiment tracking

| Criterion | **MLflow 3.x** | **ClearML** | **Comet** | **Weights & Biases** |
|---|---|---|---|---|
| License / model | Apache-2.0, fully OSS, self-host | Apache-2.0 OSS server + SDK; SaaS option | SaaS-first; Opik (LLM) is Apache-2.0 OSS | SaaS-first; self-host is enterprise/licensed |
| Self-host / air-gap | Yes — FastAPI server + SQLite/Postgres backend + local/MinIO artifacts; no external calls in OSS | Yes — Docker/K8s server; user mgmt/SSO gated to Enterprise | Self-host limited; primarily cloud | W&B Server self-host exists but enterprise-licensed |
| Native GenAI eval | Yes — `mlflow.genai.evaluate()` with built-in scorers (Correctness, RelevanceToQuery, Safety, Guidelines, RetrievalGroundedness) + `@scorer`/`make_judge()` custom judges | Limited LLM-specific eval | Via Opik | Via Weave |
| GenAI tracing | Yes — MLflow Tracing (OpenTelemetry-compatible export) | Basic | Via Opik | Via Weave |
| Custom judge via OpenAI-compatible endpoint | Yes — `provider:/model` + LiteLLM; `make_judge(..., base_url=...)`; or a local MLflow Deployments/AI Gateway endpoint (`endpoints:/my-vllm`) | via SDK | via Opik | via Weave |
| Air-gap caveat | UI historically tried to load a CDN (bootstrap/jsdelivr) on air-gapped servers — verify current version serves assets locally | Self-host fine | Cloud dependency | Cloud/licensing |

**Verdict**: **MLflow 3.x is the clear pick** — Apache-2.0, fully self-hostable with zero mandatory external calls, and in 3.x it now natively does GenAI evaluation (`mlflow.genai.evaluate`), LLM-judge scorers, tracing, and prompt versioning with registry lineage. It doubles as your results store, prompt registry, and comparison UI — which is exactly why the core stack needs no second tracing/prompt tool at the start. W&B and Comet are primarily SaaS (self-host is enterprise-gated), disqualifying them for a strict air-gap without a commercial license. ClearML is a strong OSS alternative if you also want pipeline orchestration, but its LLM-eval surface is thinner than MLflow's.

### B.3 Drift / quality monitoring

| Criterion | **Evidently** | **NannyML** | **Arize Phoenix** |
|---|---|---|---|
| License | Apache-2.0 | Apache-2.0 (OSS) | **Elastic License 2.0 (ELv2)** — source-available, self-host free, not OSI-"open source" |
| LLM descriptors | Yes — 100+ descriptors (sentiment, toxicity, length, semantic similarity, summarization quality) + LLM-judge templates (`LLMEval`, `BinaryClassificationPromptTemplate`) | No LLM-specific descriptors; focus on tabular perf estimation without labels | Yes — LLM-as-judge evaluators (hallucination, Q&A correctness, retrieval relevance, toxicity) |
| Drift detection | 20+ statistical tests (PSI, KS), embeddings drift | Strong — performance estimation & drift without ground truth | Via evals + embeddings clustering |
| Self-host / air-gap | Yes — lightweight service; storage on filesystem/SQL/S3/MinIO; Reports render to HTML/JSON offline | Yes — pure Python library | Yes — single container; docs state it can run **fully air-gapped**; OTLP ingest |
| Custom judge endpoint | Yes — uses LiteLLM (`provider`/`model`); OpenAI-compatible via LiteLLM `openai/` routing (privately-hosted endpoint param was on roadmap — verify current) | N/A | Yes — `LLM(provider="openai", model=...)` with OpenAI-compatible base URL |
| Phones home | No mandatory telemetry for evals | No | Basic web analytics; `PHOENIX_TELEMETRY_ENABLED=false` |

**Verdict**: **Evidently is the pick for drift/quality monitoring — when its trigger fires** (enough production history to define meaningful reference and current windows; see Recommended Stack). Apache-2.0, LLM descriptors purpose-built for exactly your use cases (summarization quality, toxicity, semantic similarity, custom LLM judges), renders offline HTML reports, and turns any report into a pass/fail Test Suite for CI. **NannyML** is a good complement *if* you need unlabeled performance estimation on structured signals, but it lacks LLM descriptors. **Phoenix** is excellent and air-gappable but its ELv2 license is source-available (not OSI open-source) — fine for internal self-host, but flag it if your policy requires true OSS.

### B.4 Tracing / prompt registry

| Criterion | **Langfuse** | **Arize Phoenix** | **OpenLIT** | **MLflow Tracing** | **OpenLLMetry/Traceloop** |
|---|---|---|---|---|---|
| License | **MIT** (core); enterprise modules (SCIM, audit log, data-retention) commercial | ELv2 (source-available) | Apache-2.0 | Apache-2.0 | Apache-2.0 |
| Tracing | Yes — OTLP-native, deep span model (spans/generations/events), sessions | Yes — OpenTelemetry/OpenInference, one-line auto-instrument | Yes — OTel-based library + collector | Yes — OTel-compatible | Instrumentation only (needs a backend) |
| Prompt registry/management | Yes — versioned prompts, labels, playground, link prompts↔traces | Yes — prompt management, versioning, playground | Basic | Prompt versioning | No |
| LLM-as-judge on traces | Yes — managed LLM-as-judge on prod/dev traces, scores, annotation queues | Yes — evaluators over traces/datasets | Limited | Via `mlflow.genai` scorers | No |
| Self-host / air-gap | Yes — Docker/K8s; internet access optional; deployable in air-gapped cluster | Yes — single container, air-gappable | Light (library + OTel collector) | Yes | Yes (with a backend) |
| Self-host infra | Postgres + ClickHouse + Redis + S3/MinIO (heavier) | Single Python service + Postgres (lighter) | Collector + storage | SQLite/Postgres + artifacts | Depends on backend |
| Ownership note | Acquired by **ClickHouse on 16 January 2026** (alongside ClickHouse's $400M Series D led by Dragoneer, valuing ClickHouse at $15B); founders committed the core stays MIT + self-hostable | Arize AI | OpenLIT | Databricks | Traceloop |

**Verdict**: In the core stack, **MLflow 3.x Tracing and its prompt registry cover initial needs** — no separate tracing service is deployed at the start. **Langfuse becomes the pick once production tracing, annotation workflows, and prompt *release* management become real operational requirements** — MIT-licensed core (tracing, evals, prompt management, datasets, playground all MIT with no usage caps), first-class self-hosting explicitly supporting air-gapped clusters, and the strongest prompt-registry story. It is one of the fastest-growing LLM engineering platforms — **20,470 GitHub stars, 26M+ SDK installs/month, and 6M+ Docker pulls, trusted by 19 of the Fortune 50 and 63 of the Fortune 500** (per ClickHouse's 16 Jan 2026 Series D press release). At acquisition, Langfuse's blog stated: *"Langfuse stays open source and self-hostable. There are no planned changes to licensing,"* and CEO Marc Klingen said the goal is *"a tighter end-to-end product: faster ingestion, deeper evaluation."* The cost is operational: it needs Postgres + ClickHouse + Redis + S3/MinIO. **If you want lighter ops**, Arize Phoenix is a single-container, air-gappable, OpenTelemetry-native alternative — but weigh its ELv2 (source-available) license. **OpenLIT** is viable only as a lightweight OTel instrumentation layer feeding a collector; it is not a full prompt-registry/eval platform, so it is *not* recommended as the primary.

**MLflow overlaps with Langfuse** in prompt management, tracing, and evaluation, so if/when both run, the plan must state the dividing line — see "System of record" below. Without that division, engineers will not know where the authoritative score, prompt version, or trace lives.

### RECOMMENDED STACK — lean core + trigger-based additions

#### Core (Phase 1)
| Layer | Tool | Why |
|---|---|---|
| Metrics & CI assertions | **DeepEval** | Pytest-style `assert_test`, G-Eval/DAG custom metrics, native RAG metrics, JSON-confinement for local judges |
| Experiments, artifacts, prompt versions, initial tracing | **MLflow 3.x** | Prompt registry with lineage, `mlflow.genai.evaluate`, trace-based evaluation, release evidence — no external calls |
| Model + judge access | **Direct OpenAI-compatible endpoints** | `base_url` + `api_key`; no proxy layer by default |

#### Additions — each gated by an explicit trigger
| Tool | Add when (entry criterion) |
|---|---|
| **LiteLLM proxy** | You need routing/budgets/virtual keys across many endpoints — only when necessary, not automatically |
| **Ragas** | Its dataset generation or a specific RAG metric **materially outperforms** the corresponding DeepEval/MLflow scorer for your use cases (see acceptance rule below) |
| **Langfuse** | Production tracing, annotation workflows, and prompt release management become real operational requirements |
| **Evidently** | You have enough production history to define meaningful reference and current windows for drift |

#### Acceptance rule for any second metric framework
Adopt a Ragas (or other framework's) metric only after: (a) measuring its agreement with human labels on your golden set, and (b) demonstrating added value over the corresponding DeepEval or MLflow scorer. Never run two implementations of the "same" metric in parallel — scores are not comparable across frameworks (a 0.82 "faithfulness" in one may not equal 0.82 in another), and you would maintain two metric schemas, two judge-prompt implementations, and two sets of parser failures. **One metric name → one owning framework**, recorded in `docs/metric-registry.md`.

#### System of record: MLflow vs. Langfuse (applies from the day Langfuse enters)
| Concern | Authoritative system |
|---|---|
| Model/prompt experiments, golden datasets, CI eval runs | **MLflow** |
| Artifacts & release evidence (gate results, reports) | **MLflow** |
| Prompt *development* versions & lineage | **MLflow** |
| Application observability: sessions, live traces, latency | **Langfuse** |
| User feedback, production sampling, annotation queues | **Langfuse** |
| Prompt *release/rollout* labels (prod/staging) | **Langfuse** |

Rule: offline/pre-release truth lives in MLflow; online/post-release truth lives in Langfuse. A score, prompt version, or trace has exactly one authoritative home.

#### Observability layers: OpenTelemetry and Prometheus + Grafana
> **Status: architecture documentation only — not a Phase 1 implementation item.** Nothing here is required to run the core stack. It is documented so later additions slot in without re-instrumentation or redesign.

**OpenTelemetry (OTel) — the transport standard, not a tool to "add".** It is already the wire format of the core stack: MLflow both ingests and exports traces in the OTel GenAI Semantic Convention format; the MLflow server exposes an OTLP endpoint at `/v1/traces` (ingest requires MLflow ≥ 3.6.0), export goes via `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, and `MLFLOW_ENABLE_DUAL_EXPORT=true` sends the same trace to MLflow and a second OTel backend simultaneously. Langfuse ingests OTLP as well. Practical consequence: instrument the application once with OTel semantics (or MLflow's OTel-native SDK) and, when Phase 4 arrives, place a small **OTel Collector** as the routing point. The Collector then *enforces* the system-of-record rule mechanically: CI/dev traces → MLflow, production traces → Langfuse — a routing-rule change, never a code change.

**Prometheus + Grafana — the operational metrics layer, complementary to (not part of) quality evaluation.** It is the *continuous* counterpart of the AIPerf batch performance axis: vLLM exposes a Prometheus-compatible `/metrics` endpoint by default (e2e latency, running/waiting requests, KV-cache usage, token histograms) with a reference Grafana dashboard, so model and judge endpoints are scrapeable out of the box; LiteLLM (if adopted) emits request/cost metrics via its `prometheus` callback with a maintained Grafana dashboard (verify licensing of the metrics endpoint in your pinned version); MLflow can additionally export the `mlflow.trace.span.duration` histogram via OTLP to a Prometheus started with `--web.enable-otlp-receiver`. Prometheus/Grafana for serving infra can run at any time, independently of the eval stack's phases.

**Extended system-of-record rows** (append to the table above when these layers are deployed):

| Concern | Authoritative system |
|---|---|
| Serving health: latency, throughput, saturation, GPU, alerting | **Prometheus/Grafana** |
| Per-request payloads (prompts, outputs, retrieved contexts) | **Traces (MLflow / Langfuse via OTel)** — never Prometheus |
| *Aggregated* quality signals for alerting (e.g., hourly mean faithfulness, refusal rate) | Optionally pushed to **Prometheus** as gauges — for Grafana alerts only, never as the score of record |

Two rules: (1) Prometheus stores only low-cardinality numeric aggregates — no prompt/response content, no per-request IDs as labels; (2) a Grafana alert on a quality gauge is a *trigger to investigate in MLflow/Langfuse*, not evidence itself.

---

## C) REPO BLUEPRINT — The "How, Part 2"

### Proposed directory tree for `genai-evaluation-stack`
```
genai-evaluation-stack/
├── README.md                      # overview, quickstart, links to docs/
├── .env.example                   # all env vars (endpoints, telemetry opt-outs, HF offline)
├── pyproject.toml / requirements.txt  (+ requirements.lock for air-gap)
├── docs/
│   ├── 00-concepts.md             # Section A content (what & why)
│   ├── 01-tool-comparison.md      # Section B tables
│   ├── 02-airgap-setup.md         # Section D: offline install, telemetry, HF offline
│   ├── 03-judge-endpoints.md      # base_url/api_key patterns per tool
│   ├── 04-regression-policy.md    # three-layer gate policy + pinned judge config
│   └── metric-registry.md         # one metric name → one owning framework
├── notebooks/
│   ├── 01_llm_as_judge_deepeval.ipynb      # G-Eval + AnswerRelevancy via local endpoint
│   ├── 02_rag_eval_deepeval.ipynb          # native faithfulness/contextual precision+recall
│   ├── 03_summarization_generation_quality.ipynb  # summarization + coherence/toxicity
│   ├── 04_track_results_mlflow.ipynb       # log eval runs + compare in MLflow UI
│   └── 05_drift_monitoring_evidently.ipynb # (conditional — Phase 4) descriptors + drift report
├── datasets/
│   ├── golden_qa_de.jsonl         # German Q&A golden set (schema below)
│   ├── golden_rag_de.jsonl        # RAG golden set (adds contexts/expected)
│   └── golden_summarization_de.jsonl
├── src/genai_eval/
│   ├── __init__.py
│   ├── config.py                  # env-var-first config w/ interactive fallback
│   ├── endpoints.py               # build OpenAI-compatible clients (model + judge + embed)
│   ├── datasets.py                # load/validate JSONL golden sets
│   ├── metrics.py                 # metric registry (deepeval + mlflow scorers; ragas only if accepted)
│   ├── eval_runner.py             # CLI: --dataset --model-endpoint --judge-endpoint --metrics --out
│   ├── compare_results.py         # baseline vs candidate + slice deltas, --fail-on-regression
│   └── mlflow_logging.py          # log scores/artifacts to MLflow
├── scripts/
│   ├── run_eval.sh                # bash wrapper around eval_runner (env-var-first, same pattern as run_aiperf_sustained.sh)
│   └── run_regression_gate.sh     # eval + compare + exit code, for CI and K8s Jobs
├── Dockerfile                     # containerized runner image (src + scripts) for K8s Jobs/Pods
├── pipelines/
│   ├── github-actions/eval-regression.yml
│   ├── tekton/eval-pipeline.yaml  # optional OpenShift Pipelines
│   └── k8s/eval-job.yaml          # Kubernetes Job/CronJob running the containerized eval runner
├── deploy/
│   └── k8s/                       # Kubernetes manifests / Helm values per component (to be added)
│       ├── mlflow/                # core
│       ├── langfuse/              # conditional — Phase 4
│       ├── evidently/             # conditional — Phase 4
│       ├── litellm/               # conditional — only when proxy trigger fires
│       ├── otel-collector/        # documented only — Phase 4 (trace routing: CI/dev→MLflow, prod→Langfuse)
│       └── prom-grafana/          # documented only — serving-infra metrics; independent of eval phases
└── tests/
    └── test_eval_smoke.py         # deepeval test run smoke suite for CI
```

### Golden dataset schema (JSONL, compatible with your German-prompt metadata)
Your existing fields (`id`, `language`, `category`, `prompt`, `max_tokens`, `temperature`) map cleanly onto an eval "golden". Extend with optional eval fields:

```json
{"id": "de-qa-001", "language": "de", "category": "qa", "prompt": "Was ist die Hauptstadt von Bayern?", "max_tokens": 128, "temperature": 0.0, "expected_output": "München ist die Hauptstadt von Bayern.", "contexts": ["München ist die Landeshauptstadt des Freistaats Bayern."], "metric_set": ["answer_relevancy", "faithfulness", "correctness"]}
{"id": "de-sum-014", "language": "de", "category": "summarization", "prompt": "Fasse den folgenden Text zusammen: ...", "max_tokens": 256, "temperature": 0.3, "expected_output": null, "contexts": null, "metric_set": ["summarization", "coherence", "toxicity"]}
```
- For plain chat/completion or e-mail generation, `expected_output` and `contexts` may be `null` and you rely on referenceless metrics (relevancy, coherence, toxicity, custom G-Eval rubric).
- Keep golden sets **version-controlled** and treat any change as a reviewed PR (see §D pitfalls).

### Illustrative code snippets (verify against pinned versions — APIs move fast)

**1. DeepEval custom judge via OpenAI-compatible endpoint (vLLM)**
```python
from deepeval.models import LocalModel
from deepeval.metrics import AnswerRelevancyMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

judge = LocalModel(
    model="qwen2.5-72b-instruct",           # your self-hosted judge model
    base_url="http://vllm-judge:8000/v1/",  # OpenAI-compatible endpoint
    api_key="not-needed",                   # any placeholder if no auth
    temperature=0,
)

answer_relevancy = AnswerRelevancyMetric(model=judge, threshold=0.7)
correctness = GEval(
    name="Correctness",
    criteria="Determine if 'actual output' is factually correct vs 'expected output'.",
    evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
    model=judge, threshold=0.5,
)

tc = LLMTestCase(input="Was ist die Hauptstadt von Bayern?",
                 actual_output="München.",
                 expected_output="München ist die Hauptstadt von Bayern.")
answer_relevancy.measure(tc); print(answer_relevancy.score, answer_relevancy.reason)
```
(Alternatively set once via CLI: `deepeval set-local-model --model=... --base-url="http://vllm-judge:8000/v1/" --api-key=...`.)

**2. Ragas with custom LLM + embeddings — only if accepted per the acceptance rule**
```python
from ragas import evaluate, EvaluationDataset
from ragas.metrics import Faithfulness, LLMContextRecall, ContextPrecision, AnswerRelevancy
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

judge_llm = LangchainLLMWrapper(ChatOpenAI(
    model="qwen2.5-72b-instruct",
    base_url="http://vllm-judge:8000/v1/", api_key="not-needed", temperature=0))
judge_emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
    model="bge-m3", base_url="http://vllm-embed:8000/v1/", api_key="not-needed"))

dataset = EvaluationDataset.from_list([...])  # user_input, response, retrieved_contexts, reference
result = evaluate(
    dataset=dataset,
    metrics=[Faithfulness(), AnswerRelevancy(), LLMContextRecall(), ContextPrecision()],
    llm=judge_llm, embeddings=judge_emb)
result.to_pandas().to_csv("ragas_results.csv", index=False)
```
(Newer Ragas also supports `llm_factory("qwen2.5-72b-instruct", client=AsyncOpenAI(base_url=...))`; the v0.3→v0.4 migration deprecates the wrapper classes in favor of `llm_factory` and replaces `evaluate()` with `experiment()` — verify your pinned version.)

**3. MLflow logging of eval results**
```python
import mlflow
mlflow.set_tracking_uri("http://mlflow:5000")
mlflow.set_experiment("de-rag-eval")

with mlflow.start_run(run_name="qwen72b-judge-v3prompt"):
    mlflow.log_params({"judge_model": "qwen2.5-72b", "judge_prompt_ver": "j1",
                       "dataset": "golden_rag_de.jsonl", "prompt_ver": "v3"})
    mlflow.log_metrics({"faithfulness": 0.91, "answer_relevancy": 0.88, "context_recall": 0.83})
    mlflow.log_artifact("eval_results.csv")
```
For MLflow's *native* GenAI eval with a self-hosted judge:
```python
from mlflow.genai.scorers import Correctness, RetrievalGroundedness
# self-hosted model via LiteLLM routing, e.g. "openai:/qwen", or make_judge(base_url=...)
results = mlflow.genai.evaluate(data=eval_dataset, predict_fn=my_app,
    scorers=[Correctness(model="openai:/qwen2.5-72b"), RetrievalGroundedness(model="openai:/qwen2.5-72b")])
```

**4. Evidently LLM descriptor report with a local judge (conditional — Phase 4)**
```python
import pandas as pd
from evidently import Report, Dataset, DataDefinition
from evidently.descriptors import Sentiment, TextLength, LLMEval
from evidently.presets import TextEvals
from evidently.llm.templates import BinaryClassificationPromptTemplate

correct = BinaryClassificationPromptTemplate(
    criteria="A CORRECT answer is factually accurate and addresses the question.",
    target_category="correct", non_target_category="incorrect")

df = pd.DataFrame({"question": [...], "answer": [...]})
eval_ds = Dataset.from_pandas(df, data_definition=DataDefinition(), descriptors=[
    Sentiment("answer", alias="Sentiment"),
    TextLength("answer", alias="Length"),
    LLMEval("answer", template=correct, provider="openai", model="qwen2.5-72b", alias="Correctness"),
])
rep = Report([TextEvals()]); my_eval = rep.run(eval_ds, None)
my_eval.save_html("evidently_report.html")
```
(Evidently uses LiteLLM under the hood; route to your endpoint via LiteLLM's `openai/` prefix + `api_base`. Confirm privately-hosted-endpoint support in your installed version.)

### Reusable CLI runner (env-var-first with interactive fallback)
`eval_runner.py` should follow your established pattern: read `MODEL_ENDPOINT`, `JUDGE_ENDPOINT`, `EMBED_ENDPOINT`, `API_KEY`, `DATASET`, `METRICS`, `OUT` from env; prompt interactively only when a required var is missing; be parameterized so one script serves RAG, QA, summarization, and chat scenarios.
```
python -m genai_eval.eval_runner \
  --dataset datasets/golden_rag_de.jsonl \
  --model-endpoint http://vllm-app:8000/v1 \
  --judge-endpoint http://vllm-judge:8000/v1 \
  --metrics faithfulness,answer_relevancy,context_recall \
  --out results/rag_v3.json
```
`compare_results.py` then builds a cross-scenario/version table (baseline vs candidate, delta per metric, pass/fail vs threshold) — the quality analog of a cross-scenario AIPerf compare table.

**Execution modes** (same runner, three entry points — the env-var-first design is what makes all three work unchanged):
| Mode | How | Notes |
|---|---|---|
| Local Python | `python -m genai_eval.eval_runner ...` | Development, notebooks, ad-hoc runs; interactive fallback active |
| Bash wrapper | `scripts/run_eval.sh` / `run_regression_gate.sh` | Same env-var-first → prompt → validate pattern as `run_aiperf_sustained.sh`; used by CI steps |
| Kubernetes Job/Pod | `pipelines/k8s/eval-job.yaml` running the `Dockerfile` image | Config via ConfigMap/Secret → env vars; **interactive fallback must fail fast when a required var is missing in non-TTY contexts** (K8s Jobs have no stdin); exit code = gate result, so a failed Job = failed gate; CronJob for nightly full suites |

**OpenShift notes for the eval runner.** DeepEval is a Python library, not a service — there is nothing to deploy standalone (the Confident AI platform behind it is SaaS-only, not self-hostable). "DeepEval on OpenShift" therefore means running the containerized eval runner as OpenShift Jobs/Pods or Tekton task steps. OpenShift's restricted SCC hits a few known DeepEval behaviors:

| OpenShift constraint | DeepEval behavior | Fix |
|---|---|---|
| Restricted SCC: arbitrary non-root UID, no writable `$HOME`, often read-only root FS | Writes a `.deepeval/` dir, temp test-run JSON, and a legacy JSON keystore into the **current working directory** — crashes with `[Errno 30] Read-only file system` on locked-down runtimes (upstream issues #975, #1577) | Set `WORKDIR` to a writable path and mount an `emptyDir` there; and/or `DEEPEVAL_FILE_SYSTEM=READ_ONLY`. Build the image OpenShift-style: files owned by group `0`, `chmod g=u`, no fixed UID |
| No stdin/TTY in Jobs | After interactive runs, offers to open a TUI trace inspector | `DEEPEVAL_NO_INSPECT_PROMPT=1` (documented for CI) |
| Config must come from ConfigMap/Secret env vars only | Auto-loads `.env`/`.env.local` from CWD **at import time** — a stray file in the image silently changes behavior | `DEEPEVAL_DISABLE_DOTENV=1` in the container env |
| Ephemeral pod filesystem | Locally written results vanish with the pod | Set `DEEPEVAL_RESULTS_FOLDER` (or `DisplayConfig(results_folder=...)`) to persist each run as a structured TestRun JSON — then push scores + JSON to MLflow as the system of record; pod exit code carries the gate result |
| Egress control | Telemetry/callback attempts | `DEEPEVAL_TELEMETRY_OPT_OUT=1` + a NetworkPolicy allowing egress **only** to the judge endpoint(s) and MLflow — enforcing the air-gap at cluster level |

Architecture is unchanged by this: DeepEval lives inside the runner image; only MLflow (and later Langfuse/Evidently) are actual deployed services. The K8s Job execution mode above is the right shape — it just needs these env vars plus the writable-dir/image conventions to be OpenShift-clean, and `pipelines/k8s/eval-job.yaml` should ship with them set by default.

### Regression policy (three layers; evaluated in order, any failing layer blocks the release)
1. **Hard safety constraints** — zero tolerance, no thresholds: no known PII leakage, toxicity, or policy failures on the golden set. A single failure fails the build.
2. **Per-metric floors** — absolute minimums independent of baseline: groundedness/faithfulness ≥ X, correctness ≥ Y, task completion ≥ Z (calibrated per judge model; recalibrate on judge change).
3. **Regression checks** — candidate vs. baseline: overall score delta within tolerance AND no regression beyond tolerance in any critical slice (e.g., `language=de`, per `category`). Slices come from golden-set metadata.

**Pinned judge configuration**: the judge model **and** the judge prompt version are part of the baseline definition. A layer-2 floor or layer-3 delta is only meaningful if candidate and baseline were scored by the identical judge configuration — otherwise you are measuring judge drift, not application regression. Log `judge_model` + `judge_prompt_ver` with every MLflow run; a judge change requires re-scoring the baseline and recalibrating floors.

`compare_results.py --fail-on-regression` implements layer 3; layers 1–2 map to DeepEval `assert_test` thresholds (safety metrics with threshold=1.0).

### GitHub Actions regression gate (illustrative)
```yaml
name: llm-eval-regression
on: [pull_request]
jobs:
  eval:
    runs-on: [self-hosted, airgapped]   # runner with access to internal endpoints only
    env:
      DEEPEVAL_TELEMETRY_OPT_OUT: "1"
      HF_HUB_OFFLINE: "1"
      TRANSFORMERS_OFFLINE: "1"
      JUDGE_ENDPOINT: ${{ vars.JUDGE_ENDPOINT }}
    steps:
      - uses: actions/checkout@v4
      - run: pip install --no-index --find-links=/opt/wheelhouse -r requirements.lock
      - run: deepeval test run tests/test_eval_smoke.py   # layers 1+2: exits non-zero on threshold fail
      - run: python -m genai_eval.eval_runner --dataset datasets/golden_qa_de.jsonl --metrics answer_relevancy,correctness --out results/pr.json
      - run: python -m genai_eval.compare_results --baseline results/main.json --candidate results/pr.json --fail-on-regression   # layer 3 incl. slices
```
For enterprise/OpenShift, mirror this as a **Tekton/OpenShift Pipeline** task sequence (checkout → install-from-mirror → eval → compare-gate), or run the same containerized runner as a **Kubernetes Job** (`pipelines/k8s/eval-job.yaml`) triggered by the pipeline — the CI system only launches the Job and reads its exit code. Nightly full suites map naturally to a **CronJob**. If you prefer a declarative-config approach for prompt-level regression, promptfoo's `promptfoo/promptfoo-action@v1` posts a before/after diff on each PR and supports `--repeat`/min-pass for noise control.

### Deployment (Kubernetes)
All self-hosted components will be deployed on **Kubernetes** (concrete manifests/Helm values are out of scope for now and will live under `deploy/k8s/`). Component notes:
- **MLflow (core)**: official Helm chart available; Postgres backend store + MinIO/S3 artifact store; expose behind Ingress with auth (the server is unauthenticated by default). Note the historical air-gap UI CDN issue — verify assets serve locally in your version.
- **Langfuse (conditional — Phase 4)**: official Helm chart; requires Postgres + ClickHouse + Redis + S3/MinIO; set strong `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`; internet access optional (air-gappable). All infra must run in UTC.
- **Evidently (conditional — Phase 4)**: lightweight self-hosted UI/service as a simple Deployment with filesystem/Postgres/MinIO backend.
- **LiteLLM (conditional — only when the proxy trigger fires)**: Helm chart/Deployment + Postgres; one OpenAI-compatible Service fronting all model/judge/embedding backends, with virtual keys and per-key budgets.
- **OTel Collector & Prometheus/Grafana (documented only)**: standard Kubernetes deployments (Prometheus Operator / kube-prometheus-stack fits naturally); vLLM pods are scrapeable via `/metrics` out of the box.

### Phased adoption roadmap (trigger-based, not tool-accretion-based)
- **Phase 1 — Core**: MLflow + DeepEval + direct OpenAI-compatible endpoints. PoC notebooks, golden datasets, CLI runner; all runs logged to MLflow (including `judge_model` + `judge_prompt_ver`).
- **Phase 2 — CI gates**: three-layer regression policy wired into GitHub Actions/Tekton with per-metric thresholds; smoke suite on PR, full suite nightly.
- **Phase 3 — Specialist metrics (conditional)**: evaluate Ragas candidates against the acceptance rule (human-label agreement + demonstrated added value over the corresponding DeepEval/MLflow scorer); add only winners to `docs/metric-registry.md`. **Any metric comparison and any baseline in this phase is only valid under an identical, pinned judge configuration (judge model + judge prompt version)** — comparing a Ragas score produced by one judge setup against a DeepEval score produced by another measures judge drift, not metric quality; a judge change invalidates the comparison and requires re-scoring.
- **Phase 4 — Production (conditional)**: Langfuse when annotation/release-management needs materialize; Evidently when reference/current windows exist. Introduce the OTel Collector here as the trace-routing point (CI/dev→MLflow, prod→Langfuse) and apply the MLflow/Langfuse "system of record" dividing line — including the extended Prometheus rows — from day one of Phase 4.
- **Cross-cutting, phase-independent**: Prometheus + Grafana for serving-infrastructure metrics (vLLM `/metrics`, optionally LiteLLM) can be deployed at any time; it is operational monitoring, not part of the eval stack, and is documented in the architecture for completeness.

---

## D) PRACTICAL NOTES

### Air-gap installation
- **Offline packages**: build a wheelhouse on a connected host (`pip download -r requirements.txt -d wheelhouse/`), transfer, then `pip install --no-index --find-links=wheelhouse -r requirements.lock`. Or run an internal PyPI mirror (devpi/Nexus/Artifactory). Pin exact versions — DeepEval and Ragas APIs change fast.
- **Local models for embedding/NLI/statistical metrics**: pre-download on a connected host with `snapshot_download`/`save_pretrained`, copy the HF cache to the offline box, set `HF_HOME`/`HF_HUB_CACHE`, and set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`. Verify with a tiny `AutoModel.from_pretrained("/path", local_files_only=True)` test. (Note: some libraries have bugs where `HF_HUB_OFFLINE` still tries the network — prefer explicit local paths / `local_files_only=True` as a belt-and-suspenders.)
- **Prefer LLM-judge metrics for pure air-gap**: DeepEval/MLflow LLM-judge metrics need only your local OpenAI-compatible endpoint — no HF downloads. Reserve BERTScore/ROUGE/embedding-similarity metrics for cases where you've pre-cached the weights.

### Disabling telemetry per tool (critical for air-gap)
| Tool | Setting | Notes |
|---|---|---|
| DeepEval | `DEEPEVAL_TELEMETRY_OPT_OUT=1` | Current v4.0 parses booleans case-insensitively (`1`/`true`/`yes` all work); **older 2.x releases only honored the literal `1`**, so use `1` for cross-version safety. `ERROR_REPORTING` is opt-in (leave unset). Historically it ran `blocked_by_firewall()`→google:80 and `api.ipify.org`, and initialized PostHog/Sentry — in current code these are all gated behind the opt-out (PostHog isn't even instantiated when opted out). **Avoid v2.9.4** specifically: opt-out crashed evals (`NameError: posthog`, issues #757/#1613). Optionally `DEEPEVAL_FILE_SYSTEM=READ_ONLY`. |
| Ragas (if adopted) | `RAGAS_DO_NOT_TRACK=true` | Anonymized analytics only; this fully disables (an issue is open to also honor generic `DO_NOT_TRACK`/`DISABLE_TELEMETRY`). |
| MLflow (OSS) | none needed | OSS server makes no vendor callbacks; just don't set external tracking URIs. Watch the UI-CDN air-gap issue. |
| Evidently (if adopted) | none required for evals | No mandatory telemetry for evaluation. |
| Langfuse (if adopted) | self-host env | MIT core; internet access optional/air-gappable. |
| Arize Phoenix (if used) | `PHOENIX_TELEMETRY_ENABLED=false` | Only web analytics; no trace data collected regardless. |
| HuggingFace libs | `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` | Prevent any Hub HEAD/network calls. |

### Pitfalls
- **Judge model bias**: judges favor their own family's style, verbose answers, and first-listed options in pairwise. Mitigate: use a *different, strong* judge than the model under test; use DAG/deterministic metrics where possible; cross-check against human labels.
- **Metric stability/variance**: LLM-judge scores are noisy. Set judge `temperature=0`, run multiple repeats for critical gates (promptfoo/DeepEval support repeat-with-min-pass), and track score distributions not just point values.
- **Cost/latency of LLM-as-judge**: each metric is ≥1 judge call; a big golden set × many metrics is expensive/slow. Tier suites (smoke/PR/nightly/release); cache; use a smaller judge for cheap checks and a strong judge for subjective ones.
- **Weak local judges emit invalid JSON**: DeepEval's JSON-confinement and prompt-template overrides help; Ragas returns NaN on invalid JSON — prefer DeepEval native RAG metrics or confine outputs.
- **Golden dataset versioning**: treat golden sets as code — version them, review changes in PRs, and never silently edit a baseline (or your regression gate compares against a moving target). Keep language/category metadata so you can slice scores (e.g., German-only regressions).
- **Threshold drift**: recalibrate thresholds when you change judge models — a score of 0.8 from judge A ≠ 0.8 from judge B. The judge model + judge prompt version are part of the baseline (see Regression policy); a judge change requires re-scoring the baseline.
- **Cross-framework score confusion**: never mix scores of the same metric name from different frameworks in one comparison or dashboard — enforce via `docs/metric-registry.md`.

### How this complements your existing AIPerf/k6 performance benchmarking
You already own the **performance axis**: AIPerf and k6 measure latency (TTFT, ITL), throughput (req/s, tokens/s), and concurrency behavior. This stack adds the orthogonal **quality axis**: is the output faithful, correct, relevant, coherent, safe? The two combine into a decision surface — e.g., a smaller/quantized model may win on AIPerf latency/throughput but must clear quality thresholds here before you ship it; a prompt change that improves faithfulness must not blow the latency budget AIPerf enforces. Run both in the same CI: AIPerf/k6 gate on p95 latency and throughput regressions; this stack gates on faithfulness/relevancy/correctness regressions against the golden set. Log both to MLflow so each model/prompt candidate carries a joint quality-vs-performance record for go/no-go decisions.

---

## Caveats
- **APIs change fast.** DeepEval (v4.0.5, May 2026), Ragas (v0.3→v0.4 migration replaces `evaluate()` with `experiment()` and deprecates wrapper classes for `llm_factory`), Evidently (0.7.x rewrote the API in 0.6/0.7), and MLflow GenAI (`mlflow.genai`) all move quickly. Verify import paths and signatures against the exact versions you pin before production.
- **Ragas maintenance signal**: repo activity slowed (last push early 2026) and OpenSSF Scorecard is modest (~4.4/10); it remains widely used but pin versions and vendor the wheels — another reason it is a conditional plug-in rather than core.
- **Giskard v3 is a beta rewrite**; RAGET and the vulnerability Scan still depend on v2 (no longer actively maintained). If you need RAGET now, use v2 explicitly.
- **License nuance**: Phoenix is Elastic License 2.0 (source-available, not OSI open source); Langfuse core is MIT but SCIM/audit-log/data-retention are commercial; W&B/Comet self-host is enterprise-gated. Confirm these against your organization's OSS policy.
- **MLflow air-gap UI**: historically the tracking UI attempted to load CDN assets (bootstrap/jsdelivr) on air-gapped servers; verify your version serves assets locally or pre-cache them.
- **Evidently private-endpoint judge**: routing LLM-judge to a privately-hosted OpenAI-compatible endpoint works via LiteLLM's `openai/` prefix; a dedicated custom-endpoint parameter was on the roadmap — confirm in your installed version.
- The judge LLM itself makes outbound calls to whatever endpoint you configure; "air-gapped" here means all endpoints are internal (self-hosted vLLM/LiteLLM), not that no network calls occur at all.
