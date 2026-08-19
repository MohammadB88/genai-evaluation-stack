# Metric Registry

One metric name → one owning framework. Never run two frameworks'
implementations of the same metric name in parallel; scores are not comparable
across frameworks. Adding a metric from a second framework requires passing the
acceptance rule in `genai-evaluation-stack-design.md` (human-label agreement +
demonstrated added value over the incumbent scorer).

| Metric name | Owning framework | Implementation | Requires | Threshold |
|---|---|---|---|---|
| `answer_relevancy` | DeepEval | `AnswerRelevancyMetric` | prompt, actual_output | 0.7 |
| `correctness` | DeepEval | `GEval` (custom criteria) | expected_output | 0.5 |
| `faithfulness` | DeepEval | `FaithfulnessMetric` | contexts | 0.7 |
| `contextual_precision` | DeepEval | `ContextualPrecisionMetric` | expected_output, contexts | 0.7 |
| `contextual_recall` | DeepEval | `ContextualRecallMetric` | expected_output, contexts | 0.7 |
| `summarization` | DeepEval | `SummarizationMetric` | prompt, actual_output | 0.5 |
| `toxicity` | DeepEval | `ToxicityMetric` | actual_output | 0.5 |

All Phase 1 backlog metrics are now implemented. Future additions still require
a PR that updates this table and `src/genai_eval/metrics.py` together.

The code-side source of truth is `src/genai_eval/metrics.py`; keep this file
and the registry dict in sync in the same PR.
