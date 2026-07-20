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

Registered but not yet implemented (Phase 1 backlog): `contextual_precision`,
`contextual_recall`, `summarization`, `toxicity`.

The code-side source of truth is `src/genai_eval/metrics.py`; keep this file
and the registry dict in sync in the same PR.
