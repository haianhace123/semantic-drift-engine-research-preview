# Semantic evidence coherence gate

## Failure mode

The DeepSeek response validator previously checked schema types but could
accept contradictory evidence, such as an empty `changes` array with
`overall_risk=high`, or a high-risk individual change with `overall_risk=low`.
Such an output could reach a `completed` semantic drift event and downstream
risk assessments without a coherent material-change decision.

## Contract

A successful response requires a nonempty summary; material changes require
at least one nonblank old/new meaning and a nonempty business-impact
explanation. Additions and removals may have one empty side. An empty change
list requires low overall risk; reported overall risk cannot be lower than any
individual change's risk.

The existing DeepSeek response validator remains the schema authority.
The event-field mapper runs it as a defensive boundary, including for
alternative callers and semantic recovery. Invalid responses raise
`DeepSeekSemanticDiffError`, and the existing detector persists a failed
analysis rather than asserting a completed decision. Recovery can retry these
failures within its configured retry budget.

No historical events or assessments are rewritten. No risk formula, embedding
threshold, or provider configuration is changed.

## Limits

These rules check internal coherence, not whether an AI statement is true or
supported by the source text. The synthetic decision corpus is not a
production precision/recall estimate. Source-grounding, prompt-injection
resistance, independent annotation, and uncertainty calibration require
separate evaluation before production accuracy claims.

## Regression

Run the DeepSeek and detector semantic evidence unit tests, followed by the
full unit suite. The new tests assert fail-closed behavior for contradictory
output and preserve valid additions and removals.
