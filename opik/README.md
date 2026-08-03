# Opik on Kubernetes — Install, Smoke Test, and Feature Notes

[Opik](https://github.com/comet-ml/opik) is an Apache-2.0, self-hostable LLM
observability + evaluation platform from Comet. This doc is a **learning
spike**, not a Phase decision: it walks through installing Opik on
Kubernetes via Helm, running a minimal trace + evaluation smoke test against
it, and capturing what its feature set actually covers so it can be compared
against this repo's [binding architecture](../CLAUDE.md) later if a trigger
condition ever fires.

> **Status: exploratory only.** Nothing here changes the Phase 1 stack
> (MLflow + DeepEval + direct endpoints). Opik is not adopted by this repo.
> See [Relationship to this repo's architecture](#relationship-to-this-repos-architecture)
> at the bottom.

## What is Opik?

- **Tracing**: a `@track` decorator instruments Python functions (including
  nested calls) and logs LLM calls, tool calls, and agent steps without
  framework lock-in.
- **Evaluation**: datasets + experiments + LLM-as-a-judge metrics
  (hallucination, moderation, RAG faithfulness/relevance, etc.), runnable
  from Python or via PyTest for CI.
- **Prompt management**: a prompt playground and prompt versioning.
- **Guardrails**: policy/safety checks that can run inline.
- **Production monitoring**: dashboards for feedback scores, trace volume,
  token usage, plus "online evaluation rules" (LLM-as-judge scoring of live
  traffic).

Source: [github.com/comet-ml/opik](https://github.com/comet-ml/opik),
[comet.com/docs/opik](https://www.comet.com/docs/opik/).

---

## 1. Prerequisites

- A Kubernetes cluster you can `kubectl` into (kind/minikube is fine for
  this smoke test).
- [Helm](https://helm.sh/) 3.x
- `kubectl`
- Optional: `kubectx` / `kubens` for cluster/namespace switching
- Python 3.9+ locally, for the SDK smoke test in [step 4](#4-run-a-simple-test)

## 2. Install Opik via Helm

### 2.1 Add the Helm repo

```bash
helm repo add opik https://comet-ml.github.io/opik/
helm repo update
```

### 2.2 Install the chart

```bash
VERSION=latest   # pin to a specific tag for anything beyond a throwaway test

helm upgrade --install opik -n opik --create-namespace opik/opik \
    --set component.backend.image.tag=$VERSION \
    --set component.python-backend.image.tag=$VERSION \
    --set component.python-backend.env.PYTHON_CODE_EXECUTOR_IMAGE_TAG="$VERSION" \
    --set component.frontend.image.tag=$VERSION
```

This deploys the full stack into the `opik` namespace:

| Component | Role |
|---|---|
| `backend` | Java API service (port 8080) |
| `frontend` | Web UI (port 5173) |
| `python-backend` | Python evaluation engine (port 8000) |
| `clickhouse` | trace/span analytics store (default PVC 50Gi) |
| `mysql` | state/metadata DB (default PVC 20Gi) |
| `redis` | cache (default PVC 8Gi) |
| `minio` | S3-compatible object storage (default PVC 50Gi) |
| `zookeeper` | coordination for ClickHouse |

That's ~130Gi of default PVC requests plus backend JVM heap at 80% of
container RAM — size the test cluster/nodepool accordingly, or override the
storage sizes with `--set` if you're just kicking the tires (e.g.
`--set clickhouse.persistence.size=5Gi`).

### 2.3 Watch it come up

```bash
kubectl get pods -n opik -w
```

Wait for all pods to reach `Running`/`Ready` before continuing — ClickHouse
and MySQL take the longest on first boot.

### 2.4 Access the UI

```bash
kubectl port-forward -n opik svc/opik-frontend 5173
```

Open `http://localhost:5173`.

### 2.5 (Optional) Ingress instead of port-forward

The chart exposes `ingress.enabled`, `ingress.ingressClassName`,
`ingress.hosts`, and `ingress.tls.*` per component
(`component.frontend.ingress.*`, `component.backend.ingress.*`) for a
persistent, non-port-forwarded setup. Not needed for this smoke test.

---

## 3. Get an API key / server URL for the SDK

Self-hosted Opik doesn't require a Comet Cloud account. You point the SDK
directly at the backend you just deployed. From your local machine (with
the port-forward from 2.4 still running), the backend is reachable through
the frontend's proxy at `http://localhost:5173`.

Install the SDK:

```bash
pip install opik
```

Configure it interactively:

```bash
opik configure
```

When prompted, choose "self-hosted" and give it `http://localhost:5173` (or
your ingress host). This writes `~/.opik.config`. You can skip the
interactive step entirely in CI/non-TTY contexts by setting env vars
instead (consistent with this repo's env-var-first convention — see
[CLAUDE.md](../CLAUDE.md#code-conventions)):

```bash
export OPIK_URL_OVERRIDE=http://localhost:5173/api
export OPIK_WORKSPACE=default
```

---

## 4. Run a simple test

Two smoke tests: one for tracing, one for evaluation (the two halves of
Opik's feature set).

### 4.1 Tracing smoke test

```python
# opik/smoke_trace.py
from opik import track

@track
def summarize(text: str) -> str:
    return text[:20] + "..."

@track
def pipeline(text: str) -> str:
    return summarize(text)

if __name__ == "__main__":
    result = pipeline("Opik smoke test: does tracing reach the self-hosted backend?")
    print(result)
```

```bash
python opik/smoke_trace.py
```

Then check the UI at `http://localhost:5173` → **Traces**. You should see a
`pipeline` trace with a nested `summarize` span, confirming the SDK can
reach the K8s-deployed backend end to end.

### 4.2 Evaluation smoke test

This mirrors what `src/genai_eval/eval_runner.py` does in this repo, but
using Opik's dataset/experiment/metric primitives instead of DeepEval +
MLflow.

```python
# opik/smoke_eval.py
import opik
from opik.evaluation import evaluate
from opik.evaluation.metrics import Hallucination

client = opik.Opik()

dataset = client.get_or_create_dataset(name="opik-smoke-test")
dataset.insert([
    {
        "input": "What is the capital of France?",
        "context": ["Paris is the capital and most populous city of France."],
        "expected_output": "Paris",
    },
    {
        "input": "What is the capital of Germany?",
        "context": ["Berlin is the capital and largest city of Germany."],
        "expected_output": "Berlin",
    },
])

def task(item: dict) -> dict:
    # Stand-in for a real model call — wire this to a real endpoint
    # (e.g. the same MODEL_ENDPOINT this repo's eval_runner uses) for a
    # real test.
    return {
        "input": item["input"],
        "output": item["expected_output"],
        "context": item["context"],
    }

result = evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[Hallucination()],
    experiment_name="opik-smoke-eval",
)

print(result)
```

```bash
python opik/smoke_eval.py
```

Check the UI → **Evaluation → Experiments** for `opik-smoke-eval` with
per-item Hallucination scores. Note `Hallucination` is an LLM-as-judge
metric — it will call out to an LLM (configure a judge/model endpoint per
Opik's metric docs) unless you swap in a non-LLM metric like `Equals` for a
fully offline smoke test.

---

## 5. What I learned about Opik's features

- **Tracing is the core primitive, evaluation is built on top of it.**
  `@track` captures generic function-call spans; `evaluate()` reuses the
  same trace machinery to log per-item, per-metric scores as an
  "Experiment" tied to a "Dataset" — architecturally similar to
  MLflow's `mlflow.genai.evaluate`, but traces and evals share one
  system rather than two.
- **Single deployable system of record** for both dev-time evals and
  production traces (ClickHouse-backed), whereas this repo's design
  deliberately splits that: MLflow for offline/pre-release truth, Langfuse
  (future) for online/post-release truth. Opik collapses that split into
  one tool — worth watching, but adopting it would mean revisiting the
  system-of-record boundary in [CLAUDE.md](../CLAUDE.md), not just adding a
  library.
- **Heavier deployment footprint.** The Helm chart brings in ClickHouse,
  MySQL, Redis, MinIO, and ZooKeeper by default. That's a much heavier
  footprint than this repo's current MLflow-only backend, and each extra
  stateful component is another thing to size, back up, and patch in an
  air-gapped environment.
- **LLM-as-judge metrics ship built-in** (Hallucination, Moderation, RAG
  metrics) similar in spirit to DeepEval's native metrics — a real
  comparison would need agreement-with-human-labels testing on this repo's
  golden set per the [metric-registry](../docs/metric-registry.md) adoption
  rule, not just a feature checklist.
- **CI integration via PyTest**, comparable to DeepEval's
  `deepeval test run` / `assert_test` pattern used in
  [tests/test_eval_smoke.py](../tests/test_eval_smoke.py).
- **Guardrails and prompt playground** are features DeepEval/MLflow don't
  natively provide — the closest existing coverage in this repo's roadmap
  is MLflow's prompt registry (Phase 1) for prompt versioning, and nothing
  yet for inline guardrails.

## Relationship to this repo's architecture

Per [CLAUDE.md](../CLAUDE.md), the Phase 1 core stack is **MLflow +
DeepEval + direct endpoints**, and additions are trigger-based, not
tool-accretion-based. Opik overlaps with:

- DeepEval (metrics, PyTest-based CI gating)
- MLflow (experiment/dataset tracking, prompt registry)
- Langfuse (Phase 4 production tracing — not yet triggered in this repo)

None of Opik's triggers have fired here. This directory exists purely to
record hands-on findings in case a future trigger (e.g. wanting a single
tracing+eval system instead of MLflow/Langfuse's split) makes Opik worth a
real bake-off. Any adoption would still have to go through the
[non-negotiable rules](../CLAUDE.md#non-negotiable-rules): one metric name
→ one owning framework, pinned judge config, and the system-of-record
split — none of which this spike changes.

## Cleanup

```bash
helm uninstall opik -n opik
kubectl delete namespace opik
```

This deletes the PVCs' backing claims along with the namespace (verify with
`kubectl get pvc -n opik` before/after if you want to keep the data).

## References

- [Opik GitHub](https://github.com/comet-ml/opik)
- [Opik Kubernetes install guide](https://www.comet.com/docs/opik/self-host/kubernetes/)
- [Opik Python SDK reference — evaluate()](https://www.comet.com/docs/opik/python-sdk-reference/evaluation/evaluate.html)
- [Opik quickstart](https://www.comet.com/docs/opik/quickstart)
