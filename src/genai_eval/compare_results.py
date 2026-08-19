"""Regression gate — layer 3 of the three-layer CI policy (see CLAUDE.md
"Regression Gate Policy"). Compares a candidate results JSON (eval_runner.py
output) against a baseline and applies, in order, any failure blocks:

  1. Hard safety constraints — zero tolerance: every item must pass every
     safety metric (default: toxicity) at its registry threshold. This is
     evaluated on the candidate alone; a baseline is not required.
  2. Per-metric floors — candidate aggregate mean must meet each run metric's
     REGISTRY threshold, independent of the baseline.
  3. Regression checks — candidate vs. baseline: overall mean delta within
     --tolerance, AND no slice (by language, then by category) regresses by
     more than --slice-tolerance.

Baseline resolution: an MLflow run (by --baseline-run-id, or the latest run
tagged/aliased "baseline" in --mlflow-experiment) is tried first; if MLflow
is unreachable or no tracking URI is configured, --baseline-file (a static
results JSON, same shape as eval_runner.py output) is used as fallback. Fully
missing baseline degrades to "layers 1+2 only" unless --require-baseline is
passed.

Usage:
    python -m genai_eval.compare_results --candidate results/candidate.json \
        --baseline-file results/baseline.json --fail-on-regression
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import REGISTRY

DEFAULT_SAFETY_METRICS = ("toxicity",)
DEFAULT_TOLERANCE = 0.02
DEFAULT_SLICE_TOLERANCE = 0.05

# MLflow's HTTP client has no timeout by default and can hang indefinitely
# against an unreachable tracking server, defeating the file-baseline
# fallback. Bound it so an unreachable MLflow degrades quickly instead of
# hanging the CI gate.
MLFLOW_CONNECT_TIMEOUT_SECONDS = "5"
MLFLOW_MAX_RETRIES = "1"


@dataclass
class GateViolation:
    layer: str
    message: str


@dataclass
class GateReport:
    violations: list[GateViolation] = field(default_factory=list)
    baseline_source: str | None = None

    @property
    def passed(self) -> bool:
        return not self.violations

    def add(self, layer: str, message: str) -> None:
        self.violations.append(GateViolation(layer, message))


def load_results(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_baseline_from_mlflow(
    tracking_uri: str,
    experiment: str,
    run_id: str | None = None,
) -> dict | None:
    """Fetch aggregate metrics from an MLflow run. Returns a results-shaped
    dict (only the parts the gate needs: run.metrics-equivalent + aggregates)
    or None if MLflow is unreachable/no matching run exists."""
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        return None

    import os

    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", MLFLOW_CONNECT_TIMEOUT_SECONDS)
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", MLFLOW_MAX_RETRIES)

    try:
        mlflow.set_tracking_uri(tracking_uri)
        client = MlflowClient(tracking_uri=tracking_uri)

        if run_id:
            run = client.get_run(run_id)
        else:
            exp = client.get_experiment_by_name(experiment)
            if exp is None:
                return None
            candidates = client.search_runs(
                [exp.experiment_id],
                filter_string="tags.baseline = 'true'",
                order_by=["start_time DESC"],
                max_results=1,
            )
            if not candidates:
                return None
            run = candidates[0]

        aggregates: dict[str, dict] = {}
        for key, value in run.data.metrics.items():
            if key.endswith("_mean"):
                name = key[: -len("_mean")]
                aggregates.setdefault(name, {})["mean"] = value
            elif key.endswith("_n"):
                name = key[: -len("_n")]
                aggregates.setdefault(name, {})["n"] = int(value)

        if not aggregates:
            return None
        return {"run": dict(run.data.params), "aggregates": aggregates, "items": []}
    except Exception:
        return None


def load_baseline(
    *,
    mlflow_tracking_uri: str | None,
    mlflow_experiment: str,
    baseline_run_id: str | None,
    baseline_file: str | Path | None,
) -> tuple[dict | None, str | None]:
    """Resolve the baseline: MLflow first, static JSON file fallback."""
    if mlflow_tracking_uri:
        baseline = load_baseline_from_mlflow(
            mlflow_tracking_uri, mlflow_experiment, baseline_run_id,
        )
        if baseline is not None:
            source = f"mlflow:{mlflow_tracking_uri}/{mlflow_experiment}"
            if baseline_run_id:
                source += f"/{baseline_run_id}"
            return baseline, source

    if baseline_file:
        path = Path(baseline_file)
        if path.is_file():
            return load_results(path), f"file:{path}"

    return None, None


def check_hard_safety(candidate: dict, safety_metrics: tuple[str, ...]) -> list[GateViolation]:
    """Layer 1: zero tolerance. Every item must pass every safety metric."""
    violations: list[GateViolation] = []
    for item in candidate.get("items", []):
        metrics = item.get("metrics", {})
        for name in safety_metrics:
            result = metrics.get(name)
            if result is None:
                continue
            if "error" in result:
                violations.append(GateViolation(
                    "hard_safety",
                    f"{item['id']}: {name} errored: {result['error']}",
                ))
            elif not result.get("success", False):
                violations.append(GateViolation(
                    "hard_safety",
                    f"{item['id']}: {name} failed "
                    f"(score={result.get('score')}, threshold={REGISTRY[name].threshold})",
                ))
    return violations


def check_metric_floors(candidate: dict) -> list[GateViolation]:
    """Layer 2: candidate aggregate mean must clear each metric's registry floor.
    Direction depends on MetricSpec.higher_is_better (e.g. toxicity: lower is better)."""
    violations: list[GateViolation] = []
    for name, agg in candidate.get("aggregates", {}).items():
        if name not in REGISTRY:
            continue
        spec = REGISTRY[name]
        floor = spec.threshold
        if spec.higher_is_better:
            if agg["mean"] < floor:
                violations.append(GateViolation(
                    "metric_floor",
                    f"{name}: mean={agg['mean']:.4f} below floor {floor} (n={agg['n']})",
                ))
        else:
            if agg["mean"] > floor:
                violations.append(GateViolation(
                    "metric_floor",
                    f"{name}: mean={agg['mean']:.4f} above ceiling {floor} (n={agg['n']})",
                ))
    return violations


def _slice_means(items: list[dict], key: str) -> dict[str, dict[str, list[float]]]:
    """slice_value -> metric_name -> [scores]"""
    out: dict[str, dict[str, list[float]]] = {}
    for item in items:
        slice_value = item.get(key)
        if slice_value is None:
            continue
        for name, result in item.get("metrics", {}).items():
            score = result.get("score")
            if score is None:
                continue
            out.setdefault(slice_value, {}).setdefault(name, []).append(score)
    return out


def check_regression(
    candidate: dict,
    baseline: dict,
    *,
    tolerance: float,
    slice_tolerance: float,
) -> list[GateViolation]:
    """Layer 3: overall delta within tolerance, no critical-slice regression."""
    violations: list[GateViolation] = []

    cand_agg = candidate.get("aggregates", {})
    base_agg = baseline.get("aggregates", {})
    for name, cand in cand_agg.items():
        base = base_agg.get(name)
        if base is None:
            continue
        higher_is_better = REGISTRY[name].higher_is_better if name in REGISTRY else True
        raw_delta = cand["mean"] - base["mean"]
        # Normalize so "delta" is always signed toward worse-is-negative.
        delta = raw_delta if higher_is_better else -raw_delta
        if delta < -tolerance:
            violations.append(GateViolation(
                "regression_overall",
                f"{name}: {cand['mean']:.4f} vs baseline {base['mean']:.4f} "
                f"(delta={delta:.4f}, tolerance=-{tolerance})",
            ))

    cand_items = candidate.get("items", [])
    base_items = baseline.get("items", [])
    if not base_items:
        return violations

    for slice_key in ("language", "category"):
        cand_slices = _slice_means(cand_items, slice_key)
        base_slices = _slice_means(base_items, slice_key)
        for slice_value, cand_metrics in cand_slices.items():
            base_metrics = base_slices.get(slice_value)
            if not base_metrics:
                continue
            for name, cand_scores in cand_metrics.items():
                base_scores = base_metrics.get(name)
                if not base_scores:
                    continue
                higher_is_better = REGISTRY[name].higher_is_better if name in REGISTRY else True
                cand_mean = sum(cand_scores) / len(cand_scores)
                base_mean = sum(base_scores) / len(base_scores)
                raw_delta = cand_mean - base_mean
                delta = raw_delta if higher_is_better else -raw_delta
                if delta < -slice_tolerance:
                    violations.append(GateViolation(
                        "regression_slice",
                        f"{slice_key}={slice_value}, {name}: {cand_mean:.4f} vs "
                        f"baseline {base_mean:.4f} (delta={delta:.4f}, "
                        f"tolerance=-{slice_tolerance})",
                    ))
    return violations


def run_gate(
    candidate: dict,
    *,
    mlflow_tracking_uri: str | None,
    mlflow_experiment: str,
    baseline_run_id: str | None,
    baseline_file: str | Path | None,
    safety_metrics: tuple[str, ...],
    tolerance: float,
    slice_tolerance: float,
    require_baseline: bool,
) -> GateReport:
    report = GateReport()

    for v in check_hard_safety(candidate, safety_metrics):
        report.add(v.layer, v.message)
    for v in check_metric_floors(candidate):
        report.add(v.layer, v.message)

    baseline, source = load_baseline(
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_experiment=mlflow_experiment,
        baseline_run_id=baseline_run_id,
        baseline_file=baseline_file,
    )
    report.baseline_source = source

    if baseline is None:
        if require_baseline:
            report.add("regression", "No baseline available (MLflow and file both failed) "
                                       "and --require-baseline was set.")
        return report

    for v in check_regression(candidate, baseline, tolerance=tolerance,
                               slice_tolerance=slice_tolerance):
        report.add(v.layer, v.message)
    return report


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genai_eval.compare_results",
        description="Regression gate: candidate results vs. baseline (MLflow or static JSON).",
    )
    parser.add_argument("--candidate", required=True, help="Path to candidate results JSON")
    parser.add_argument("--baseline-file", help="Static baseline results JSON (fallback)")
    parser.add_argument("--baseline-run-id", help="MLflow run ID to use as baseline")
    parser.add_argument("--mlflow-tracking-uri",
                        help="MLflow tracking URI (env: MLFLOW_TRACKING_URI)")
    parser.add_argument("--mlflow-experiment", default="genai-eval",
                        help="MLflow experiment name (default: genai-eval)")
    parser.add_argument("--safety-metrics", default=",".join(DEFAULT_SAFETY_METRICS),
                        help=f"Comma-separated zero-tolerance metrics (default: "
                             f"{','.join(DEFAULT_SAFETY_METRICS)})")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help=f"Max allowed overall mean drop (default: {DEFAULT_TOLERANCE})")
    parser.add_argument("--slice-tolerance", type=float, default=DEFAULT_SLICE_TOLERANCE,
                        help=f"Max allowed per-slice mean drop (default: {DEFAULT_SLICE_TOLERANCE})")
    parser.add_argument("--require-baseline", action="store_true",
                        help="Fail the gate if no baseline can be resolved")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit non-zero if any gate layer fails (otherwise report only)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    import os

    args = parse_args(argv)
    candidate = load_results(args.candidate)
    safety_metrics = tuple(m.strip() for m in args.safety_metrics.split(",") if m.strip())

    report = run_gate(
        candidate,
        mlflow_tracking_uri=args.mlflow_tracking_uri or os.environ.get("MLFLOW_TRACKING_URI"),
        mlflow_experiment=args.mlflow_experiment,
        baseline_run_id=args.baseline_run_id,
        baseline_file=args.baseline_file,
        safety_metrics=safety_metrics,
        tolerance=args.tolerance,
        slice_tolerance=args.slice_tolerance,
        require_baseline=args.require_baseline,
    )

    print(f"Baseline source: {report.baseline_source or '(none)'}")
    if report.passed:
        print("Regression gate: PASSED")
        return 0

    print(f"Regression gate: FAILED ({len(report.violations)} violation(s))")
    for v in report.violations:
        print(f"  [{v.layer}] {v.message}")

    return 1 if args.fail_on_regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
