"""CI smoke suite — the fastest tier of the regression gate (see CLAUDE.md
"Regression Gate Policy": smoke on every PR, broader nightly, full suite
before major prompt/model changes).

Requires live MODEL_ENDPOINT + JUDGE_ENDPOINT; skipped entirely when they're
not configured so `pytest tests/` stays offline-safe by default (test_offline.py
covers the no-endpoint path). Runs a small, cheap metric slice against
golden_qa_de.jsonl only — broader datasets and the full metric set belong to
the nightly/full tiers, not here.
"""

import os
from pathlib import Path

import pytest

from genai_eval.compare_results import run_gate
from genai_eval.config import RunnerConfig
from genai_eval.eval_runner import run_evaluation

REPO_ROOT = Path(__file__).resolve().parents[1]

requires_live_endpoints = pytest.mark.skipif(
    not (os.environ.get("MODEL_ENDPOINT") and os.environ.get("JUDGE_ENDPOINT")),
    reason="MODEL_ENDPOINT/JUDGE_ENDPOINT not set — smoke suite needs live endpoints",
)


@pytest.fixture
def smoke_config(tmp_path) -> RunnerConfig:
    return RunnerConfig(
        dataset=str(REPO_ROOT / "datasets" / "golden_qa_de.jsonl"),
        model_endpoint=os.environ["MODEL_ENDPOINT"],
        model_name=os.environ.get("MODEL_NAME", "my-app-model"),
        judge_endpoint=os.environ["JUDGE_ENDPOINT"],
        judge_model=os.environ.get("JUDGE_MODEL", "my-judge-model"),
        api_key=os.environ.get("API_KEY", "not-needed"),
        metrics=("answer_relevancy",),
        out=str(tmp_path / "smoke_results.json"),
        mlflow_tracking_uri=None,
        mlflow_experiment="genai-eval-smoke",
    )


@requires_live_endpoints
def test_smoke_eval_runs_end_to_end(smoke_config):
    payload = run_evaluation(smoke_config)
    assert payload["run"]["n_items"] > 0
    assert payload["aggregates"], "smoke run produced no metric scores"


@requires_live_endpoints
def test_smoke_eval_passes_hard_safety_and_floor_gates(smoke_config):
    payload = run_evaluation(smoke_config)
    report = run_gate(
        payload,
        mlflow_tracking_uri=None,
        mlflow_experiment=smoke_config.mlflow_experiment,
        baseline_run_id=None,
        baseline_file=None,
        safety_metrics=("toxicity",),
        tolerance=0.02,
        slice_tolerance=0.05,
        require_baseline=False,
    )
    safety_and_floor = [
        v for v in report.violations if v.layer in ("hard_safety", "metric_floor")
    ]
    assert not safety_and_floor, f"smoke gate failures: {safety_and_floor}"
