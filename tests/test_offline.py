"""Offline tests — no endpoints, no judge, no network required.

The DeepEval smoke suite that exercises real endpoints (tests/test_eval_smoke.py)
arrives in Phase 2; these tests guard the parts that must work everywhere:
dataset validation, config fail-fast behavior, and registry consistency.
"""

import json
from pathlib import Path

import pytest

from genai_eval.config import MissingConfigError, resolve
from genai_eval.datasets import DatasetError, GoldenItem, load_golden_dataset
from genai_eval.eval_runner import metrics_for_item, parse_args
from genai_eval.metrics import REGISTRY, validate_metric_names

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- golden dataset ---------------------------------------------------------

def test_golden_qa_de_loads_and_is_valid():
    items = load_golden_dataset(REPO_ROOT / "datasets" / "golden_qa_de.jsonl")
    assert len(items) >= 6
    assert all(i.language == "de" for i in items)
    assert len({i.id for i in items}) == len(items)
    # every metric referenced by the dataset must exist in the registry
    for item in items:
        for name in item.metric_set or ():
            assert name in REGISTRY, f"{item.id} references unregistered metric {name}"


def test_rag_items_have_contexts():
    items = load_golden_dataset(REPO_ROOT / "datasets" / "golden_qa_de.jsonl")
    for item in items:
        if "faithfulness" in (item.metric_set or ()):
            assert item.contexts, f"{item.id} wants faithfulness but has no contexts"


def test_missing_required_field_raises(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "x", "language": "de", "prompt": "p"}) + "\n",
                   encoding="utf-8")
    with pytest.raises(DatasetError, match="missing required fields"):
        load_golden_dataset(bad)


def test_duplicate_id_raises(tmp_path):
    row = {"id": "dup", "language": "de", "category": "qa", "prompt": "p",
           "max_tokens": 10, "temperature": 0.0}
    bad = tmp_path / "dup.jsonl"
    bad.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="duplicate id"):
        load_golden_dataset(bad)


def test_invalid_json_raises(tmp_path):
    bad = tmp_path / "broken.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid JSON"):
        load_golden_dataset(bad)


# --- config fail-fast -------------------------------------------------------

def test_required_setting_fails_fast_without_tty(monkeypatch):
    monkeypatch.delenv("MODEL_ENDPOINT", raising=False)
    # pytest's captured stdin is not a TTY, matching CI/K8s Job conditions
    with pytest.raises(MissingConfigError):
        resolve("MODEL_ENDPOINT")


def test_env_var_wins_over_default(monkeypatch):
    monkeypatch.setenv("MODEL_ENDPOINT", "http://example:9999/v1")
    assert resolve("MODEL_ENDPOINT", default="http://localhost:8000/v1") \
        == "http://example:9999/v1"


def test_cli_value_wins_over_env(monkeypatch):
    monkeypatch.setenv("MODEL_ENDPOINT", "http://from-env/v1")
    assert resolve("MODEL_ENDPOINT", "http://from-cli/v1") == "http://from-cli/v1"


# --- metric registry & selection --------------------------------------------

def test_registry_matches_docs_metric_registry():
    doc = (REPO_ROOT / "docs" / "metric-registry.md").read_text(encoding="utf-8")
    for name in REGISTRY:
        assert f"`{name}`" in doc, f"{name} missing from docs/metric-registry.md"


def test_unknown_metric_rejected():
    with pytest.raises(KeyError, match="Unknown metric"):
        validate_metric_names(("answer_relevancy", "nonexistent_metric"))


def test_metrics_for_item_skips_when_fields_missing():
    item = GoldenItem(id="t1", language="de", category="qa", prompt="p",
                      max_tokens=10, temperature=0.0,
                      expected_output=None, contexts=None,
                      metric_set=("answer_relevancy", "correctness", "faithfulness"))
    to_run, skipped = metrics_for_item(item, ("answer_relevancy", "correctness", "faithfulness"))
    assert to_run == ["answer_relevancy"]
    assert len(skipped) == 2  # correctness lacks expected_output, faithfulness lacks contexts


def test_metrics_for_item_respects_item_metric_set():
    item = GoldenItem(id="t2", language="de", category="qa", prompt="p",
                      max_tokens=10, temperature=0.0,
                      expected_output="e", metric_set=("correctness",))
    to_run, skipped = metrics_for_item(item, ("answer_relevancy", "correctness"))
    assert to_run == ["correctness"]
    assert any("not in item metric_set" in s for s in skipped)


# --- CLI --------------------------------------------------------------------

def test_parse_args_accepts_all_flags():
    args = parse_args([
        "--dataset", "d.jsonl", "--model-endpoint", "http://m/v1",
        "--model-name", "m", "--judge-endpoint", "http://j/v1",
        "--judge-model", "j", "--metrics", "answer_relevancy", "--out", "o.json",
    ])
    assert args.dataset == "d.jsonl"
    assert args.judge_model == "j"
