"""CLI eval runner: golden dataset -> app model -> DeepEval metrics -> JSON (+ MLflow).

Usage:
    python -m genai_eval.eval_runner --dataset datasets/golden_qa_de.jsonl \
        --metrics answer_relevancy,correctness --out results/qa.json

Every flag falls back to its env var (MODEL_ENDPOINT, JUDGE_ENDPOINT, ...),
then to an interactive prompt on a TTY; in non-TTY contexts missing required
settings fail fast. Exit codes: 0 = run completed, 1 = run failed,
2 = configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import MissingConfigError, RunnerConfig, load_runner_config
from .datasets import GoldenItem, load_golden_dataset
from .endpoints import build_judge, build_model_client, generate_answer
from .metrics import REGISTRY, validate_metric_names


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genai_eval.eval_runner",
        description="Run DeepEval metrics over a golden dataset via OpenAI-compatible endpoints.",
    )
    parser.add_argument("--dataset", help="Path to golden JSONL (env: DATASET)")
    parser.add_argument("--model-endpoint", dest="model_endpoint",
                        help="App model base URL (env: MODEL_ENDPOINT)")
    parser.add_argument("--model-name", dest="model_name",
                        help="App model name (env: MODEL_NAME)")
    parser.add_argument("--judge-endpoint", dest="judge_endpoint",
                        help="Judge base URL (env: JUDGE_ENDPOINT)")
    parser.add_argument("--judge-model", dest="judge_model",
                        help="Judge model name (env: JUDGE_MODEL)")
    parser.add_argument("--api-key", dest="api_key", help="API key (env: API_KEY)")
    parser.add_argument("--metrics", help="Comma-separated metric names (env: METRICS)")
    parser.add_argument("--out", help="Output JSON path (env: OUT)")
    return parser.parse_args(argv)


def metrics_for_item(item: GoldenItem, requested: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Select metrics to run for one item; returns (to_run, skipped_reasons)."""
    to_run: list[str] = []
    skipped: list[str] = []
    for name in requested:
        if item.metric_set is not None and name not in item.metric_set:
            skipped.append(f"{name}: not in item metric_set")
            continue
        missing = [f for f in REGISTRY[name].requires if getattr(item, f) is None]
        if missing:
            skipped.append(f"{name}: item lacks {missing}")
            continue
        to_run.append(name)
    return to_run, skipped


def build_test_case(item: GoldenItem, actual_output: str):
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input=item.prompt,
        actual_output=actual_output,
        expected_output=item.expected_output,
        retrieval_context=list(item.contexts) if item.contexts else None,
    )


def judge_prompt_version() -> str:
    """Pinned-judge identifier: DeepEval's default metric prompts change with
    the library version, so the installed version *is* the prompt version."""
    import deepeval

    return f"deepeval-default@{deepeval.__version__}"


def run_evaluation(cfg: RunnerConfig) -> dict:
    items = load_golden_dataset(cfg.dataset)
    validate_metric_names(cfg.metrics)
    model_client = build_model_client(cfg)
    judge = build_judge(cfg)

    item_results: list[dict] = []
    scores: dict[str, list[float]] = {name: [] for name in cfg.metrics}
    generation_failures = 0

    for item in items:
        row: dict = {"id": item.id, "language": item.language,
                     "category": item.category, "prompt": item.prompt}
        try:
            actual = generate_answer(model_client, cfg.model_name, item)
        except Exception as exc:  # endpoint/network errors must not kill the run
            generation_failures += 1
            row["generation_error"] = f"{type(exc).__name__}: {exc}"
            item_results.append(row)
            print(f"[{item.id}] generation FAILED: {row['generation_error']}", file=sys.stderr)
            continue

        row["actual_output"] = actual
        to_run, skipped = metrics_for_item(item, cfg.metrics)
        row["skipped_metrics"] = skipped
        row["metrics"] = {}
        test_case = build_test_case(item, actual)

        for name in to_run:
            metric = REGISTRY[name].factory(judge)
            try:
                metric.measure(test_case)
                row["metrics"][name] = {
                    "score": metric.score,
                    "success": metric.is_successful(),
                    "reason": getattr(metric, "reason", None),
                }
                if metric.score is not None:
                    scores[name].append(metric.score)
            except Exception as exc:
                row["metrics"][name] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"[{item.id}] metric {name} FAILED: {exc}", file=sys.stderr)

        summary = ", ".join(
            f"{n}={v.get('score')}" for n, v in row["metrics"].items()
        ) or "no metrics run"
        print(f"[{item.id}] {summary}")
        item_results.append(row)

    aggregates = {
        name: {"mean": sum(vals) / len(vals), "n": len(vals)}
        for name, vals in scores.items() if vals
    }
    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dataset": cfg.dataset,
            "model_endpoint": cfg.model_endpoint,
            "model_name": cfg.model_name,
            "judge_endpoint": cfg.judge_endpoint,
            "judge_model": cfg.judge_model,
            "judge_prompt_ver": judge_prompt_version(),
            "metrics": list(cfg.metrics),
            "n_items": len(items),
            "n_generation_failures": generation_failures,
        },
        "aggregates": aggregates,
        "items": item_results,
    }


def main(argv=None) -> int:
    try:
        cfg = load_runner_config(parse_args(argv))
    except MissingConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        payload = run_evaluation(cfg)
    except Exception as exc:
        print(f"Evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    out_path = Path(cfg.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")

    for name, agg in payload["aggregates"].items():
        print(f"  {name}: mean={agg['mean']:.3f} (n={agg['n']})")
    if not payload["aggregates"]:
        print("  No metric scores produced — check endpoint/generation errors above.",
              file=sys.stderr)
        return 1

    if cfg.mlflow_tracking_uri:
        from .mlflow_logging import log_eval_run

        run_id = log_eval_run(cfg.mlflow_tracking_uri, cfg.mlflow_experiment,
                              payload, out_path)
        print(f"Logged to MLflow run {run_id} ({cfg.mlflow_tracking_uri})")
    else:
        print("MLFLOW_TRACKING_URI not set — skipping MLflow logging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
