# Independently labeled semantic holdout evaluation (PR.9)

## Purpose and current evidence

SDE's pinned 24-pair synthetic diagnostic (`scripts.evaluate_semantic_decisions`)
remains useful for repeatable contract checks. It is not independent ground
truth. This companion runner accepts an **externally prepared** corpus and
executes the same production semantic analysis path, without creating drift
events or changing database state.

**No externally labeled corpus is bundled with the repository.** Do not treat
this capability or a passing unit test as measured customer-document accuracy.
The manifest declares annotation provenance; the runner cannot verify that
annotators were independent, the held-out split was frozen, or the sample
represents a future customer workload. Keep the annotation protocol and
adjudication records outside this repository for review.

## Private corpus contract

Create an **untracked** JSON file outside the repository. Replace the example
below with consented, de-identified old/new document pairs. The text shown is
synthetic and is only a schema illustration, **not** a holdout dataset.

```json
{
  "schema_version": 1,
  "dataset_id": "anonymous_policy_holdout",
  "provenance": {
    "source_type": "internal",
    "annotation_protocol_id": "blind_review_v1",
    "annotator_count": 2,
    "adjudicated": true,
    "held_out_before_model_selection": true
  },
  "cases": [
    {
      "case_id": "anonymous_case_001",
      "domain": "policy",
      "expected_material": true,
      "old_text": "Refunds are allowed within 30 days.",
      "new_text": "Refunds are allowed within 3 days."
    },
    {
      "case_id": "anonymous_case_002",
      "domain": "policy",
      "expected_material": false,
      "old_text": "We delete records after 30 days.",
      "new_text": "Records are removed following a 30-day period."
    }
  ]
}
```

Corpus requirements: 2–100 **distinct** document pairs, both material and
non-material examples, one case ID per pair, at most 12,000 characters per
version and 5 MB JSON total. IDs, domains and annotation protocol identifiers
must be anonymous lowercase machine identifiers. Exact or reversed duplicate
pairs are rejected. Do not use full customer names, email addresses or internal
document paths as case IDs. Avoid storing the corpus in Git, CI artifacts or
permanent logs. Create legally approved samples before a live run.

`source_type` may be `public`, `internal`, or `customer_authorized`; all three
are merely declarations. An annotation protocol should document how
materiality was defined, disagreements were adjudicated, exclusions were
chosen, and which model/prompt versions annotators were blind to. Review
prompt-injection and adversarial examples as a separate evaluation slice.

## Offline plan (zero provider requests)

Run from the project root using a mounted directory that contains the private
file, or use an already mounted test-only file. This example assumes the file
is outside the repo at `C:\HaiAnh\sde_eval\holdout.json`:

```powershell
$corpusDirectory = "C:\HaiAnh\sde_eval"
docker compose run --rm `
    -v "${PWD}:/app" `
    -v "${corpusDirectory}:/holdout:ro" `
    api python -m scripts.evaluate_semantic_holdout `
    --corpus /holdout/holdout.json
```

Plan mode prints only a dataset ID, corpus SHA-256, label counts, declared
provenance status, and maximum requests. It makes **no** DeepSeek call.

## Optional live evaluation

**Only after dataset transfer approval**, with test/development credentials
and available provider credits. The model sees the actual old/new text.
Replace the explicit request budget with the plan's `case_count` (all cases
must be assessed; silently sampling would invalidate cohort metrics).

```powershell
$env:SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS = "1"
$env:SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED = "1"
try {
    docker compose run --rm `
        -e SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS `
        -e SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED `
        -v "${PWD}:/app" `
        -v "${corpusDirectory}:/holdout:ro" `
        api python -m scripts.evaluate_semantic_holdout `
        --corpus /holdout/holdout.json --execute `
        --max-provider-requests 2
} finally {
    Remove-Item Env:SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS `
        -ErrorAction SilentlyContinue
    Remove-Item Env:SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED `
        -ErrorAction SilentlyContinue
}
```

The CLI also requires `ENVIRONMENT=development|test` and a configured
`DEEPSEEK_API_KEY`. Never run this command with production credentials or
unapproved customer content. The budget bounds case-level compare calls;
network/provider retries may result in more HTTP attempts. The report does not
include document text, full provider responses or exception messages, but does
include anonymous case IDs and labels. Do not log original documents in other
layers of the request pipeline.

## Reading the output

The report includes pooled and per-domain confusion matrices, precision,
recall, F1 on **completed** cases, coverage, and class-specific detection
yields whose denominators also include abstentions. Failed provider requests
and invalid AI responses are abstentions, not correct negatives. In slices
without enough positives, metrics are `null` rather than invented values.

The exact-text-diff comparator flags every non-identical pair as material. It
is an intentionally weak sanity control, not a tuned lexical or embedding
baseline; an embedding comparison requires separate threshold selection on a
non-holdout development set. The result has no automatic accuracy `PASS` flag,
no confidence intervals and no production release claim. Before deciding on a
release threshold, gather representative independently adjudicated samples,
pre-register evaluation criteria and audit errors at the evidence level.
