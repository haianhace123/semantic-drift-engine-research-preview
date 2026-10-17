# Semantic decision evaluation (P2D.2.4)

## Purpose

The previous embedding sensitivity check measured *distance ordering*. It did not
establish whether SDE correctly identifies meaningful changes. This evaluation
exercises the actual changed-region extractor, DeepSeek comparison, and
pre-persistence event-field mapping. The target is **binary semantic materiality**:
`changes_count > 0` means the model found a meaningful change. A `low` overall
risk may still accompany a meaningful change and is **not** a negative label.

The pinned corpus at `tests/fixtures/semantic_decision_v1.json` includes 24
synthetic old/new pairs across pricing, eligibility, privacy, security, SLA and
policy. Twelve pairs deliberately change meaning; twelve preserve meaning.
Cases include changes to negation, dates, scope, numeric terms and exceptions,
as well as paraphrasing, formatting and paragraph reordering controls.
These labels were assigned when the examples were authored; they have **not**
been independently annotated or validated against customer documents.

## Safe plan (no provider calls)

```powershell
docker compose run --rm -v "${PWD}:/app" api `
    python -m scripts.evaluate_semantic_decisions
```

The plan prints the corpus SHA-256, case IDs, class counts and the maximum number
of requests. It does not connect to DeepSeek, use snapshot files or modify a DB.

## Optional live evaluation

This is **not** part of routine regression tests. It makes **24 live DeepSeek
requests**, potentially consuming paid provider credits. Run it only if a
non-production environment is configured with `DEEPSEEK_API_KEY` and the current
model is known to be accessible. The evaluation does not call Gemini and does not
write drift events, risk assessments or downstream actions.

```powershell
$env:SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS = "1"
try {
    docker compose run --rm --no-deps `
        -e ENVIRONMENT=test `
        -e SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS `
        -v "${PWD}:/app:ro" `
        api python -m scripts.evaluate_semantic_decisions --execute

    $evalExitCode = $LASTEXITCODE
    Write-Host "Semantic decision evaluation exit code: $evalExitCode"
} finally {
    Remove-Item Env:SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS `
        -ErrorAction SilentlyContinue
}
```

The process requires an explicitly configured `ENVIRONMENT=development` or
`ENVIRONMENT=test` and the opt-in flag before constructing a provider client.
An unset environment is rejected instead of silently assuming development. Do not run this on production credentials,
customer data or production databases. Do not print environment dumps or secrets.

## Reading the report

The report contains a confusion matrix (`TP`, `FP`, `TN`, `FN`) and separately
counts abstentions on positive and negative labels. Provider failures, malformed
answers and internally inconsistent results are **abstentions**, never correct
negatives. Precision and recall on completed cases exclude abstentions; the
additional `material_detection_yield` includes them in its denominator. A zero
numerator produces `null` for precision/recall when a denominator is absent.

The CLI returns exit code `0` only if all 24 labels match with no abstentions;
`2` indicates a diagnostic mismatch or incomplete case; other nonzero codes
indicate command/configuration errors. These are strict fixture checks, **not**
production accuracy thresholds or a guarantee of generalization. The report
includes corpus SHA-256, model, case IDs and outcomes but not the old/new text,
full model responses or provider error messages.

Review failing cases before considering any prompt or model change. Do not tune
against this set and then claim it is independent test evidence. A credible
production release gate will additionally require independently labeled,
representative samples, multiple domains, reproducible adjudication and a held-out
set. This diagnostic batch adds no new classification authority or risk weights.
