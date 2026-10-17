# Controlled semantic change sensitivity

This is a pre-production **model sensitivity probe**, not a production drift detector acceptance test. It compares three manually reviewed policy cases. Each case has a baseline, a meaning-preserving paraphrase, and a material policy reversal. The check requires the material change's cosine distance from baseline to exceed the paraphrase's cosine distance. It uses the production `EmbeddingService` and `calculate_vector_metrics` implementation, without writing to PostgreSQL, changing snapshots, producing decisions, or allowing downstream actions.

## Run

Run only in a development/test environment. The command performs **nine live Gemini embedding requests** and therefore may consume provider quota. No OpenAI fallback is permitted. The API service must already have `EMBEDDING_PROVIDER=gemini` and `GEMINI_API_KEY` configured. Do not print either credential or your full Docker environment.

```powershell
$env:SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS = "1"
try {
    docker compose run --rm `
        -e SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS `
        -v "${PWD}:/app" `
        api python -m scripts.evaluate_semantic_change_sensitivity --execute

    $benchmarkExitCode = $LASTEXITCODE
    Write-Host "Semantic benchmark exit code: $benchmarkExitCode"
} finally {
    Remove-Item Env:SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS -ErrorAction SilentlyContinue
}
```

Exit code `0` means all three **pairwise separation checks** passed. Exit code `2` means at least one contrast was not correctly ordered; the command still prints the complete JSON report. Other nonzero exit codes indicate configuration, provider, or validation errors. A failed contrast is a model-quality finding, not a reason to silently lower thresholds or rewrite the sample after looking at its score.

The JSON report intentionally excludes benchmark text, document content, API keys and the embedding vectors. It contains case IDs, model identity, distances, separation margins and pass/fail. Keep the report for analysis. Do not treat this as precision/recall, risk-scoring validation or evidence of authorized invalidation.

## Interpretation and next gate

A positive ranking is **necessary but insufficient**: even correctly ordered distances may overlap broadly on real customer data. The next gate requires a larger blinded set with documented labels, multiple domains and class balance, testing the actual event detector and Risk v2 decisions on persisted snapshots. Do not ship any threshold adjustment based solely on these three curated contrasts.
