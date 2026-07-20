"""Log evaluation runs to MLflow — the offline system of record.

Every run must carry judge_model + judge_prompt_ver (pinned-judge rule):
scores from different judge configurations are not comparable.
"""

from __future__ import annotations

from pathlib import Path


def log_eval_run(
    tracking_uri: str,
    experiment: str,
    run_payload: dict,
    results_path: str | Path,
) -> str:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)

    run_meta = run_payload["run"]
    aggregates = run_payload["aggregates"]

    run_name = f"{Path(run_meta['dataset']).stem}-{run_meta['model_name']}"
    with mlflow.start_run(run_name=run_name) as active_run:
        mlflow.log_params({
            "dataset": run_meta["dataset"],
            "model_name": run_meta["model_name"],
            "model_endpoint": run_meta["model_endpoint"],
            "judge_model": run_meta["judge_model"],
            "judge_prompt_ver": run_meta["judge_prompt_ver"],
            "metrics": ",".join(run_meta["metrics"]),
            "n_items": run_meta["n_items"],
            "n_generation_failures": run_meta["n_generation_failures"],
        })
        mlflow.log_metrics({
            f"{name}_mean": agg["mean"] for name, agg in aggregates.items()
        })
        mlflow.log_metrics({
            f"{name}_n": agg["n"] for name, agg in aggregates.items()
        })
        mlflow.log_artifact(str(results_path))
        return active_run.info.run_id
