# Semantic provider execution provenance

## Product risk

A completed semantic event previously stored `llm_tokens_used=0` regardless of the
OpenAI-compatible provider's reported token usage. That value could be
mistaken for measured zero consumption during customer pilots. In addition,
the default `deepseek-v4-flash` model name is a retired alias and may route to
a newer model without an application release. Evidence quality and operating
cost cannot be evaluated reliably if the executed model and reported usage are
indistinguishable from configuration assumptions.

## Contract

- The existing default model identifier `deepseek-v4-flash` remains unchanged
  for this batch. DeepSeek currently routes this retired alias to V4.1-Flash,
  while `deepseek-flash` is the documented canonical identifier. For the
  controlled pilot, explicitly choose `DEEPSEEK_MODEL=deepseek-flash` and
  measure behavior before changing the application default in a separate
  deployment decision. This avoids an unmeasured semantic-model change.
- On a successfully validated provider response, SDE stores
  `semantic_delta_json.provider_execution`: `requested_model`,
  `reported_model` (nullable), `usage_reported`, and `total_tokens` (nullable).
  The reported model is taken from the SDK response, not the model-authored
  JSON body. Missing or invalid usage is **unknown**, not measured zero.
- When `usage_reported=true`, `llm_tokens_used` is the provider-reported total
  token count. When usage is absent, the NOT NULL database field stays zero for
  compatibility; consumers must check `usage_reported` before treating that
  value as observed usage. Historical zero values cannot be backfilled from
  nonexistent provider telemetry.
- Metadata is kept outside `semantic_analysis`. The existing semantic result
  validator remains the authority for changes, risk and completion status.
  Initial detection and recovery share the same field-mapping function.
- The presence of usage metadata does **not** establish billing cost, inference
  latency, model checkpoint identity, or semantic accuracy. A provider's
  reported model may identify a serving family rather than a pinned revision.
  No prices or token-to-currency conversions are inferred.

The provider currently enables thinking by default, according to its
contemporary documentation. This patch deliberately does not change thinking
mode, temperature, prompts, output limits or model quality assumptions without
controlled evaluation. Verify actual requests, latency and charges during the
approved pilot, then benchmark quality against the independent labeled holdout.

## Verification

Run `tests/unit/test_semantic_provider_execution_provenance.py` and the existing
DeepSeek/semantic recovery regression tests. PostgreSQL acceptance should
confirm persisted `llm_tokens_used` and `semantic_delta_json.provider_execution`
using controlled SDK responses; a live approved call is still required before
claiming actual token metering or semantic product readiness.

First-party API references (consult at deployment time):

- https://api-docs.deepseek.com/news/news260910/
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
