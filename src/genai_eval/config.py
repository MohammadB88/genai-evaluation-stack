"""Env-var-first configuration with interactive fallback.

Resolution order for every setting: CLI argument > environment variable >
interactive prompt (only on a TTY) > hard failure. Non-TTY contexts (CI,
Kubernetes Jobs — no stdin) must fail fast instead of hanging on input().
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


class MissingConfigError(RuntimeError):
    """A required setting is absent and cannot be prompted for."""


def resolve(
    env_var: str,
    cli_value: str | None = None,
    *,
    default: str | None = None,
    required: bool = True,
    prompt: str | None = None,
) -> str | None:
    """Resolve one setting: CLI value > env var > TTY prompt > default > error."""
    if cli_value:
        return cli_value
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    if sys.stdin is not None and sys.stdin.isatty():
        entered = input(f"{prompt or env_var} [{default or 'required'}]: ").strip()
        if entered:
            return entered
    if default is not None:
        return default
    if required:
        raise MissingConfigError(
            f"Required setting {env_var} is not set (no CLI flag, no env var, "
            f"and no TTY for interactive input)."
        )
    return None


@dataclass(frozen=True)
class RunnerConfig:
    dataset: str
    model_endpoint: str
    model_name: str
    judge_endpoint: str
    judge_model: str
    api_key: str
    metrics: tuple[str, ...]
    out: str
    mlflow_tracking_uri: str | None
    mlflow_experiment: str


def load_runner_config(args) -> RunnerConfig:
    """Build the runner config from argparse args + environment."""
    metrics_raw = resolve(
        "METRICS", getattr(args, "metrics", None),
        default="answer_relevancy,correctness", prompt="Metrics (comma-separated)",
    )
    return RunnerConfig(
        dataset=resolve("DATASET", getattr(args, "dataset", None),
                        prompt="Path to golden dataset (JSONL)"),
        model_endpoint=resolve("MODEL_ENDPOINT", getattr(args, "model_endpoint", None),
                               prompt="Model endpoint (OpenAI-compatible base URL)"),
        model_name=resolve("MODEL_NAME", getattr(args, "model_name", None),
                           prompt="Model name (as served by the endpoint)"),
        judge_endpoint=resolve("JUDGE_ENDPOINT", getattr(args, "judge_endpoint", None),
                               prompt="Judge endpoint (OpenAI-compatible base URL)"),
        judge_model=resolve("JUDGE_MODEL", getattr(args, "judge_model", None),
                            prompt="Judge model name"),
        api_key=resolve("API_KEY", getattr(args, "api_key", None), default="not-needed"),
        metrics=tuple(m.strip() for m in metrics_raw.split(",") if m.strip()),
        out=resolve("OUT", getattr(args, "out", None),
                    default="results/eval_results.json", prompt="Output JSON path"),
        mlflow_tracking_uri=resolve("MLFLOW_TRACKING_URI", None,
                                    required=False),
        mlflow_experiment=resolve("MLFLOW_EXPERIMENT", None,
                                  default="genai-eval", required=False),
    )
