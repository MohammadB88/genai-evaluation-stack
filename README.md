# genai-evaluation-stack

Self-hosted, air-gap-friendly evaluation stack for GenAI/LLM applications.
Phase 1 core: **DeepEval** (metrics, LLM-as-judge) + **MLflow 3.x** (experiment
tracking) + direct OpenAI-compatible endpoints. Design rationale, tool
comparison, and roadmap: [genai-evaluation-stack-design.md](genai-evaluation-stack-design.md).

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

cp .env.example .env               # then edit endpoints/model names
set -a; source .env; set +a        # bash. PowerShell: see below
```

PowerShell equivalent of exporting `.env`:

```powershell
Get-Content .env | Where-Object { $_ -match '^\s*[^#]' } | ForEach-Object {
  $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v.Trim()
}
```

> **Running from Jupyter/JupyterLab**: a `!bash` or `%%bash` cell's working
> directory doesn't always match the notebook's location, so a relative
> `source .env` can fail with "No such file or directory". Use the absolute
> path instead, e.g. `source /full/path/to/genai-evaluation-stack/.env`
> (or `source "$(pwd)/.env"` if the notebook's cwd is confirmed to be the repo root).

Run an evaluation (all flags fall back to env vars, then to an interactive
prompt on a TTY; missing required values fail fast in CI/K8s):

```bash
python -m genai_eval.eval_runner \
  --dataset datasets/golden_qa_de.jsonl \
  --metrics answer_relevancy,correctness \
  --out results/qa_baseline.json
```

Scores are logged to MLflow when `MLFLOW_TRACKING_URI` is set (always including
`judge_model` and `judge_prompt_ver` — see the pinned-judge rule in
[CLAUDE.md](CLAUDE.md)).

Offline tests (no endpoints needed):

```bash
pytest tests/
```

## Endpoints

The stack assumes OpenAI-compatible endpoints (vLLM, Ollama, LiteLLM, cloud):

| Env var | Role |
|---|---|
| `MODEL_ENDPOINT` / `MODEL_NAME` | Application model under evaluation |
| `JUDGE_ENDPOINT` / `JUDGE_MODEL` | LLM-as-judge for DeepEval metrics |
| `API_KEY` | Shared key; any placeholder if the endpoint has no auth |

The defaults in `.env.example` are localhost placeholders.

## Metric registry

One metric name → one owning framework. See
[docs/metric-registry.md](docs/metric-registry.md) before adding or renaming
any metric.
