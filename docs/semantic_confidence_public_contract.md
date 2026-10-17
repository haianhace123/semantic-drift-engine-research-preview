# Semantic event confidence: public read-model contract

## Current contract

The numeric `confidence` field in the legacy dashboard and temporal event
responses remains for wire compatibility. Its calibration status is now explicit:
`confidence_calibration_status: "not_established"`.

A valid JSON response or a completed semantic analysis is not an observed
probability that its semantic judgment is correct. The current detector uses
response-structure completeness to estimate a numeric value; older stored
values may not have verifiable provenance. The public response must not call
these values calibrated probability or model accuracy.

## Compatibility and responsibility

This additive field does not alter event IDs, snapshot provenance, event
persistence, risk classification, Risk v1/v2/v3 scores, downstream invalidation,
consumer admission or historical assessment reproduction. The existing
`confidence` field is not removed or reinterpreted. Clients must treat it as a
legacy diagnostic / versioned scoring input and must not use it as an accuracy
estimate. `not_established` applies even when the numeric confidence is zero.

## Release gate for a new risk policy

Risk v2 remains live authority; Risk v3 remains shadow. A future correction to
confidence contribution requires a newly versioned and separately accepted
scoring policy, group-disjoint human-labeled ground truth, tenant-scoped
read-only impact analysis, rollback/reader compatibility and downstream
admission regressions. The isolated Product 13 synthetic cohort proves
threshold sensitivity, not customer prevalence or detector calibration.

This document deliberately makes no promise that any existing score is
calibrated. No database migration is needed for the additive read-model field.
