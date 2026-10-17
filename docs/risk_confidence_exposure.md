# Risk confidence exposure (P2D.2.5)

## Decision and rationale

The change detector currently assigns a deterministic value of `0.90` to
structurally valid DeepSeek responses. This value is **not** calibrated model
confidence or a measured probability of semantic correctness. A JSON document
satisfying a schema does not establish 90% confidence in its classification.

The production `deterministic_v2` assessment uses that field as a 10%-weight
contribution. For a typical `0.90` value, it adds `0.09` to the 0..1 risk score.
Risk v3 currently uses the same contribution in its shadow scoring. Severity
boundaries are set by `risk_classification_service`: 0.40 (medium), 0.65
(high), 0.85 (critical). Therefore schema completion alone can move an
assessment across a severity boundary.

**Do not change the v2 or v3 formula in place.** They are immutable scoring
contracts used by persisted evidence, replay, comparison and downstream alerting.
A new scoring policy requires a new version and qualification, not a silent
change to existing weights. Confidence provenance and calibrated confidence must
not be conflated with the degree of risk.

## Counterfactual

The audit uses the persisted score and the persisted confidence contribution:

`counterfactual_score = max(0, persisted_score - persisted_confidence_contribution)`

It then classifies both with the canonical downstream severity thresholds.
This counterfactual is a **diagnostic ablation**, not the score for a proposed
future risk version; it does not redistribute the 10% weight. It does not
reassess old events or make any downstream decision.

Reports include sample size, truncation, mean/p95/maximum contribution in score
points, number and rate of severity-band changes, and aggregate severity
transitions. Results are separated by scoring version. No event IDs, page URLs,
document text, secrets, or per-event data are returned.

## Safe plan

```powershell
docker compose run --rm --no-deps `
    -v "${PWD}:/app:ro" `
    api python -m scripts.evaluate_risk_confidence_exposure
```

Plan mode reads no database and calls no model provider.

## Optional read-only audit

The database-backed audit requires an explicit tenant organization UUID and
opt-in environment flag. Use a development/staging database with representative
assessments, or explicitly authorized read-only production access if permitted
by your operating policy. It enforces `SET TRANSACTION READ ONLY` before
querying. The audit never modifies the source database, but the DB connection
itself can still create operational load.

```powershell
$env:SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ = "1"
try {
    docker compose run --rm `
        -e SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ `
        -v "${PWD}:/app:ro" `
        api python -m scripts.evaluate_risk_confidence_exposure `
        --execute --organization-id "REPLACE-WITH-ORGANIZATION-UUID" `
        --limit 500
} finally {
    Remove-Item Env:SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ `
        -ErrorAction SilentlyContinue
}
```

`--limit` caps reads **per risk version** at 500 by default, maximum 5000.
The audit fetches one extra row to detect truncation and reports the most
recent assessments by `assessed_at, id`. If `truncated` is true, the results
are for the sample only and must not be represented as tenant-wide metrics.
No rows produce `null` for average or rate, never a misleading zero.

## Closing this batch and next decision

This batch is complete after focused/full unit tests pass and a dry plan executes
without a DB connection. A representative read-only DB execution is optional
but strongly recommended before making any scoring-policy change. The next
versioned scoring candidate should be evaluated using held-out, independently
labeled evidence and shadow comparison with v2, including severity/alert
movement. No migration or historical score rewrite is authorized by this audit.
