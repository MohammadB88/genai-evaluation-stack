"""Metric registry — the code-side source of truth mirrored in docs/metric-registry.md.

One metric name → one owning framework (currently all DeepEval). Metric
instances are created fresh per test case because DeepEval metrics hold state
(score/reason) after measure().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MetricSpec:
    name: str
    owner: str
    threshold: float
    # Golden-item fields the metric needs beyond prompt + actual_output.
    requires: tuple[str, ...]
    factory: Callable[[Any], Any]  # judge -> metric instance


def _answer_relevancy(judge):
    from deepeval.metrics import AnswerRelevancyMetric

    return AnswerRelevancyMetric(model=judge, threshold=0.7)


def _correctness(judge):
    from deepeval.metrics import GEval
    from deepeval.test_case import SingleTurnParams

    return GEval(
        name="Correctness",
        criteria=(
            "Determine whether the 'actual output' is factually consistent "
            "with the 'expected output'. Penalize contradictions and fabricated "
            "facts; do not penalize valid paraphrases or different phrasing."
        ),
        evaluation_params=[
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=0.5,
    )


def _faithfulness(judge):
    from deepeval.metrics import FaithfulnessMetric

    return FaithfulnessMetric(model=judge, threshold=0.7)


def _contextual_precision(judge):
    from deepeval.metrics import ContextualPrecisionMetric

    return ContextualPrecisionMetric(model=judge, threshold=0.7)


def _contextual_recall(judge):
    from deepeval.metrics import ContextualRecallMetric

    return ContextualRecallMetric(model=judge, threshold=0.7)


def _summarization(judge):
    from deepeval.metrics import SummarizationMetric

    return SummarizationMetric(model=judge, threshold=0.5)


def _toxicity(judge):
    from deepeval.metrics import ToxicityMetric

    return ToxicityMetric(model=judge, threshold=0.5)


REGISTRY: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        MetricSpec("answer_relevancy", "deepeval", 0.7, (), _answer_relevancy),
        MetricSpec("correctness", "deepeval", 0.5, ("expected_output",), _correctness),
        MetricSpec("faithfulness", "deepeval", 0.7, ("contexts",), _faithfulness),
        MetricSpec("contextual_precision", "deepeval", 0.7,
                   ("expected_output", "contexts"), _contextual_precision),
        MetricSpec("contextual_recall", "deepeval", 0.7,
                   ("expected_output", "contexts"), _contextual_recall),
        MetricSpec("summarization", "deepeval", 0.5, (), _summarization),
        MetricSpec("toxicity", "deepeval", 0.5, (), _toxicity),
    )
}


def validate_metric_names(names: tuple[str, ...]) -> None:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(
            f"Unknown metric(s) {unknown}; registered: {sorted(REGISTRY)}. "
            f"New metrics must be added to docs/metric-registry.md and this registry."
        )
