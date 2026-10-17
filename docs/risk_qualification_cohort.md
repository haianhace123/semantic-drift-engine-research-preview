# Risk scoring qualification cohort (P2D.2.6)

## Purpose

The P2D.2.5 audit showed that removing the persisted, uncalibrated confidence contribution would move 22 of 89 v2 assessments from medium to low; the same count appeared in v3. This is an exposure finding, not proof of incorrect severity. The two version counts are not independent observations.

This cohort audit answers a narrower, necessary question: are the persisted assessments adequately paired by **event**, and do they contain comparable topology evidence? It also identifies which event IDs need independent human severity review before changing a scoring policy.

## Contract

- `deterministic_v2` supplies the baseline cohort, bounded to the latest N assessments **ordered by assessed_at**. Shadow `deterministic_v3` rows are fetched only for the selected v2 event IDs. Each non-null event reference must resolve to an event owned by the same organization; missing or cross-tenant references abort the audit.
- Report distinct linked baseline events, missing shadow assessments, unlinked assessments and pairing coverage. Multiple rows for the same event/version cause a hard error rather than silent averaging.
- A topology signal with no previous state is a baseline, **not** an observed topology change. Count comparable states and positive topology contribution separately.
- Recalculate the confidence-removal *exposure* using persisted values and the canonical severity bands; do not alter scores or reallocate weights.
- Risk v2/v3 assessments do not provide human-validated ground truth. Report `independent_severity_labels=0` and `model_accuracy_evaluated=false`, even when there is perfect version agreement.
- Default output is aggregate only. `--include-review-event-ids` is a separate, explicit opt-in to emit up to 50 IDs from the confidence-sensitive v2 events. There is no raw document content in the output. Treat event IDs as tenant-sensitive operational identifiers.
- The audit is read-only within one PostgreSQL `REPEATABLE READ, READ ONLY` transaction and scoped by a required organization UUID. The script never calls providers or executes downstream actions.

## Run

Plan mode (no DB access):

```powershell
 docker compose run --rm --no-deps -v "${PWD}:/app:ro" api python -m scripts.evaluate_risk_qualification_cohort
```

Read-only audit (use the organization's real ID):

```powershell
$env:SDE_RISK_QUALIFICATION_ALLOW_DB_READ = "1"
try {
    docker compose run --rm `
        -e SDE_RISK_QUALIFICATION_ALLOW_DB_READ `
        -v "${PWD}:/app:ro" `
        api python -m scripts.evaluate_risk_qualification_cohort `
        --execute --organization-id "YOUR-ORGANIZATION-UUID" --limit 500
    if ($LASTEXITCODE -ne 0) { throw "Qualification audit failed." }
}
finally {
    Remove-Item Env:SDE_RISK_QUALIFICATION_ALLOW_DB_READ -ErrorAction SilentlyContinue
}
```

Run the optional review-ID export by adding `--include-review-event-ids` inside the same opt-in execution. Do not commit its JSON output to Git, and do not confuse version agreement with independently labeled correctness.

## Interpreting results

- `paired_cohort_available_not_ground_truth` means every sampled v2 row has an event ID and a paired v3 assessment. It **does not** qualify v3 for production promotion.
- `incomplete_event_pairing` indicates missing/unlinked assessment evidence. `bounded_sample_only` means the configured limit truncated the cohort.
- `confidence_band_flips` may include all v2 assessments, while `paired_version_severity_disagreement` only includes paired events. Never compare the latter rate against the former without checking the denominators.
- The next product decision requires a separately reviewed severity-label set from real customer events and a versioned candidate scoring policy. Until that exists, historical v2/v3 assessments and active alert behavior must remain unchanged.
