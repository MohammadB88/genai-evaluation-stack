"""Offline tests for the regression gate (compare_results.py). No endpoints,
no MLflow server, no network — pure logic over synthetic results payloads.
"""

import json

from genai_eval.compare_results import (
    check_hard_safety,
    check_metric_floors,
    check_regression,
    load_baseline,
    run_gate,
)


def make_item(id_, language, category, metrics):
    return {"id": id_, "language": language, "category": category, "metrics": metrics}


def make_results(items, aggregates=None):
    if aggregates is None:
        aggregates = {}
        for item in items:
            for name, result in item["metrics"].items():
                if "score" in result:
                    aggregates.setdefault(name, {"scores": []})["scores"].append(result["score"])
        aggregates = {
            name: {"mean": sum(v["scores"]) / len(v["scores"]), "n": len(v["scores"])}
            for name, v in aggregates.items()
        }
    return {"run": {}, "aggregates": aggregates, "items": items}


# --- layer 1: hard safety ----------------------------------------------------

def test_hard_safety_passes_when_all_items_pass():
    candidate = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.0, "success": True}}),
        make_item("b", "de", "qa", {"toxicity": {"score": 0.1, "success": True}}),
    ])
    assert check_hard_safety(candidate, ("toxicity",)) == []


def test_hard_safety_flags_any_failing_item():
    candidate = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.9, "success": False}}),
    ])
    violations = check_hard_safety(candidate, ("toxicity",))
    assert len(violations) == 1
    assert "a" in violations[0].message


def test_hard_safety_flags_metric_errors():
    candidate = make_results([
        make_item("a", "de", "qa", {"toxicity": {"error": "TimeoutError: judge unreachable"}}),
    ])
    violations = check_hard_safety(candidate, ("toxicity",))
    assert len(violations) == 1
    assert "errored" in violations[0].message


def test_hard_safety_ignores_metrics_not_run():
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.9, "success": True}}),
    ])
    assert check_hard_safety(candidate, ("toxicity",)) == []


# --- layer 2: per-metric floors ----------------------------------------------

def test_metric_floor_passes_at_or_above_threshold():
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.8, "success": True}}),
    ])
    assert check_metric_floors(candidate) == []


def test_metric_floor_flags_mean_below_threshold():
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.3, "success": False}}),
    ])
    violations = check_metric_floors(candidate)
    assert len(violations) == 1
    assert "answer_relevancy" in violations[0].message


def test_metric_floor_ignores_unregistered_metric_names():
    candidate = {"aggregates": {"made_up_metric": {"mean": 0.0, "n": 1}}, "items": []}
    assert check_metric_floors(candidate) == []


def test_metric_floor_toxicity_is_lower_is_better():
    # toxicity's registry threshold (0.5) is a ceiling, not a floor: low score = good.
    low_toxicity = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.1, "success": True}}),
    ])
    assert check_metric_floors(low_toxicity) == []

    high_toxicity = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.9, "success": False}}),
    ])
    violations = check_metric_floors(high_toxicity)
    assert len(violations) == 1
    assert "above ceiling" in violations[0].message


# --- layer 3: regression checks ----------------------------------------------

def test_regression_passes_within_tolerance():
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.80, "success": True}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.81, "success": True}}),
    ])
    violations = check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05)
    assert violations == []


def test_regression_flags_overall_drop_beyond_tolerance():
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.60, "success": True}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.80, "success": True}}),
    ])
    violations = check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05)
    assert any(v.layer == "regression_overall" for v in violations)


def test_regression_flags_critical_slice_drop_even_if_overall_ok():
    # overall mean stable (two items average out) but the "de" slice alone regresses
    candidate = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.50, "success": False}}),
        make_item("b", "en", "qa", {"answer_relevancy": {"score": 0.95, "success": True}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.80, "success": True}}),
        make_item("b", "en", "qa", {"answer_relevancy": {"score": 0.80, "success": True}}),
    ])
    violations = check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05)
    assert any(v.layer == "regression_slice" and "language=de" in v.message for v in violations)


def test_regression_toxicity_increase_is_a_regression():
    # toxicity going up (worse) should flag even though the raw delta is positive.
    candidate = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.40, "success": True}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.05, "success": True}}),
    ])
    violations = check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05)
    assert any(v.layer == "regression_overall" and "toxicity" in v.message for v in violations)


def test_regression_toxicity_decrease_is_not_a_regression():
    # toxicity going down (better) should never be flagged.
    candidate = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.05, "success": True}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.40, "success": True}}),
    ])
    violations = check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05)
    assert violations == []


def test_regression_skips_metrics_or_slices_absent_from_baseline():
    candidate = make_results([
        make_item("a", "de", "qa", {"faithfulness": {"score": 0.5, "success": False}}),
    ])
    baseline = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.9, "success": True}}),
    ])
    assert check_regression(candidate, baseline, tolerance=0.02, slice_tolerance=0.05) == []


# --- baseline resolution ------------------------------------------------------

def test_load_baseline_falls_back_to_file_when_no_mlflow_uri(tmp_path):
    baseline_payload = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.8, "success": True}}),
    ])
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline_payload), encoding="utf-8")

    baseline, source = load_baseline(
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=path,
    )
    assert baseline is not None
    assert source == f"file:{path}"


def test_load_baseline_returns_none_when_nothing_resolves():
    baseline, source = load_baseline(
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=None,
    )
    assert baseline is None
    assert source is None


def test_load_baseline_ignores_unreachable_mlflow_and_uses_file_fallback(tmp_path):
    baseline_payload = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.8, "success": True}}),
    ])
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline_payload), encoding="utf-8")

    baseline, source = load_baseline(
        mlflow_tracking_uri="http://localhost:1/nonexistent",
        mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=path,
    )
    assert baseline is not None
    assert source == f"file:{path}"


# --- run_gate end-to-end (offline, file baseline only) -----------------------

def test_run_gate_passes_clean_candidate_against_file_baseline(tmp_path):
    payload = make_results([
        make_item("a", "de", "qa", {
            "answer_relevancy": {"score": 0.85, "success": True},
            "toxicity": {"score": 0.0, "success": True},
        }),
    ])
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")

    report = run_gate(
        payload,
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=baseline_path,
        safety_metrics=("toxicity",), tolerance=0.02, slice_tolerance=0.05,
        require_baseline=False,
    )
    assert report.passed


def test_run_gate_fails_on_hard_safety_regardless_of_baseline():
    payload = make_results([
        make_item("a", "de", "qa", {"toxicity": {"score": 0.9, "success": False}}),
    ])
    report = run_gate(
        payload,
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=None,
        safety_metrics=("toxicity",), tolerance=0.02, slice_tolerance=0.05,
        require_baseline=False,
    )
    assert not report.passed
    assert any(v.layer == "hard_safety" for v in report.violations)


def test_run_gate_missing_baseline_does_not_fail_unless_required():
    payload = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.9, "success": True}}),
    ])
    report = run_gate(
        payload,
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=None,
        safety_metrics=("toxicity",), tolerance=0.02, slice_tolerance=0.05,
        require_baseline=False,
    )
    assert report.passed
    assert report.baseline_source is None


def test_run_gate_missing_baseline_fails_when_required():
    payload = make_results([
        make_item("a", "de", "qa", {"answer_relevancy": {"score": 0.9, "success": True}}),
    ])
    report = run_gate(
        payload,
        mlflow_tracking_uri=None, mlflow_experiment="genai-eval",
        baseline_run_id=None, baseline_file=None,
        safety_metrics=("toxicity",), tolerance=0.02, slice_tolerance=0.05,
        require_baseline=True,
    )
    assert not report.passed
