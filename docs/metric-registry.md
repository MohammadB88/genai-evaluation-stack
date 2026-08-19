# Metric Registry

One metric name → one owning framework. Never run two frameworks'
implementations of the same metric name in parallel; scores are not comparable
across frameworks. Adding a metric from a second framework requires passing the
acceptance rule in `genai-evaluation-stack-design.md` (human-label agreement +
demonstrated added value over the incumbent scorer).

| Metric name | Owning framework | Implementation | Requires | Threshold | Direction |
|---|---|---|---|---|---|
| `answer_relevancy` | DeepEval | `AnswerRelevancyMetric` | prompt, actual_output | 0.7 | higher is better |
| `correctness` | DeepEval | `GEval` (custom criteria) | expected_output | 0.5 | higher is better |
| `faithfulness` | DeepEval | `FaithfulnessMetric` | contexts | 0.7 | higher is better |
| `contextual_precision` | DeepEval | `ContextualPrecisionMetric` | expected_output, contexts | 0.7 | higher is better |
| `contextual_recall` | DeepEval | `ContextualRecallMetric` | expected_output, contexts | 0.7 | higher is better |
| `summarization` | DeepEval | `SummarizationMetric` | prompt, actual_output | 0.5 | higher is better |
| `toxicity` | DeepEval | `ToxicityMetric` | actual_output | 0.5 | **lower is better** |

All Phase 1 backlog metrics are now implemented. Future additions still require
a PR that updates this table and `src/genai_eval/metrics.py` together.

`Direction` mirrors `MetricSpec.higher_is_better` in `src/genai_eval/metrics.py`
and the corresponding DeepEval metric's own `is_successful()` (e.g. `ToxicityMetric`
passes when `score <= threshold`, the opposite of every other metric here). The
regression gate (`src/genai_eval/compare_results.py`) reads this field to decide
whether a threshold is a floor or a ceiling, and which direction counts as a
regression — get it wrong here and the CI gate will pass exactly the runs it
should block.

The code-side source of truth is `src/genai_eval/metrics.py`; keep this file
and the registry dict in sync in the same PR.
