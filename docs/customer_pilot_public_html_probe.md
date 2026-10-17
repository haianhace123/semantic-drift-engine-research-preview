# Public HTML pilot source probe

## Purpose

Before enrolling a public URL, an operator can verify whether the production
**generic HTML** crawler can fetch and extract its text. URL syntax validation
alone cannot tell whether the remote response is HTML, exceeds the 1 MiB
response cap, extracts fewer than 100 useful characters, or produces unstable
normalized content. The probe uses the same HTTPS/DNS-pinned transport,
same-host redirect policy and HTML cleaner as the production crawler.

This is a diagnostic tool, not source enrollment, an uptime check, a
semantic-drift evaluation, a provider smoke test or customer acceptance
certification. A successful probe proves only that the selected public source
was retrievable and extractable **at the time sampled**. It does not create
monitored pages, immutable snapshots, semantic events or downstream effects.

## Safety and execution

Only probe public documents you are authorized to request. Respect site terms
and choose a reasonable frequency. Never provide authenticated/private URLs,
or material that requires browser login. The generic enrollment contract rejects
query strings, fragments, credentials, private IP URLs and non-443 HTTPS ports.
Dedicated OpenAI/Google/Slack sources are handled by their own adapters, not
this probe.

The default invocation is a **network-free dry run**:

```powershell
 docker compose run --rm -v "${PWD}:/app" api python -m scripts.probe_public_html_source `
   --url "https://www.rfc-editor.org/rfc/rfc2119.html"
```

To check the actual public endpoint from the development container (one HTTPS
request, no database or provider call):

```powershell
 docker compose run --rm -v "${PWD}:/app" api python -m scripts.probe_public_html_source `
   --url "https://www.rfc-editor.org/rfc/rfc2119.html" --execute
```

Two samples can reveal short-term dynamic-content churn. They are separated
by at least two seconds; this is not a synthetic content revision:

```powershell
 docker compose run --rm -v "${PWD}:/app" api python -m scripts.probe_public_html_source `
   --url "https://www.rfc-editor.org/rfc/rfc2119.html" --execute `
   --samples 2 --interval-seconds 5
```

Outbound execution is forbidden when `ENVIRONMENT` is not `development` or
`test`. Results are JSON and contain only normalized character counts,
SHA-256 hashes and a stability signal; no HTML, extracted text, HTTP response
body or provider exception is emitted. DNS/HTTP errors are reduced to safe
categories. Network access is explicit; the probe never calls DeepSeek,
embeddings or other AI providers.

## Interpretation and go/no-go

- `mode=dry_run` means only enrollment-style URL validation succeeded.
- `outcome=eligible` means the production fetch and extraction size limits
  accepted each sample. This does **not** mean the source has been enrolled.
- `content_stable_between_samples=false` is a warning to inspect dynamic page
  content; it is not semantic change evidence.
- `content_rejected`, `fetch_failed` or `blocked_destination` mean the generic
  crawler could not admit the sample as-is. Do not force enrollment around
  network restrictions or truncate evidence to make a test green.
- Exit code `2` denotes an expected ineligibility or probe failure. Do not
  ignore it when choosing a pilot source.

## Remaining real-world acceptance

After this preflight passes, enroll a **permitted, operator-controlled** page
through the React UI. Let the scheduler persist an initial snapshot; publish a
second authorized revision; verify that the next crawl persists a distinct
hash. Inspect source diagnostics, semantic event state and worker operations.
Provider requests with customer text require explicit data-transfer approval
and a separate request budget; the existing external labeled holdout tool
provides that opt-in evaluation boundary. Exact downstream impact requires
published lineage evidence and must not be inferred from a website URL.
