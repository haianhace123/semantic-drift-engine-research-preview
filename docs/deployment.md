# Semantic Drift Engine Deployment

## 1. Deployment Status

The repository contains a deployable API and several worker processes, but the current runtime should not yet be described as a fully autonomous production knowledge-invalidation platform.

This document distinguishes:

- implemented code,
- processes currently deployed by Docker Compose,
- runtime capabilities still requiring closure.

## 2. Container Runtime

The Docker image is based on:

```text
python:3.12-slim-bookworm
```

The image installs:

- Python dependencies from `requirements.txt`,
- PostgreSQL client tooling,
- Playwright Chromium and its runtime dependencies.

The application source is then copied into `/app`.

## 3. Base Compose Topology

The base `docker-compose.yml` currently defines:

```text
db
integration_downstream_db (integration profile)
redis
api
vector_worker
alert_delivery_worker
crawler_scheduler
lineage_invalidation_executor
email_worker
```

### PostgreSQL

The database image is based on PostgreSQL 15 with pgvector support.

Persistent database state is stored in a Docker volume.

### Redis

Redis is configured with append-only persistence and is used by runtime coordination and queue-related functionality.

### API

The API runs:

```text
uvicorn src.main:app
```

with configurable worker count and forwarded-proxy handling.

### Vector worker

The vector worker runs:

```text
python -m scripts.vector_worker
```

Its stages can be enabled or disabled independently.

Embedding generation is disabled by default in Compose because it depends on an external provider and quota.

### Alert delivery worker

The alert delivery worker runs:

```text
python -m scripts.alert_delivery_worker
```

It processes asynchronous alert delivery work with retry/claim behavior implemented by the worker.


### Email worker

The email worker runs:

```text
python -m scripts.email_worker
```

It consumes transactional email jobs from a Redis Stream, uses a consumer group for delivery ownership, recovers abandoned pending jobs, schedules bounded retries through a Redis sorted set, and moves exhausted or permanent failures to a dead-letter stream.

Email delivery uses Resend. `RESEND_API_KEY` and `EMAIL_FROM` are required for the worker to start.

The email worker is opt-in (`email` Compose profile). A default `docker compose up -d`
does not start it when Resend has not been configured. To enable transactional
email delivery, configure both values in the local `.env` or the deployment's
secret manager, then run:

```sh
docker compose --profile email up -d email_worker
```

If the worker was created by an older deployment and is already restart-looping,
stop it until delivery credentials are configured:

```sh
docker compose --profile email stop email_worker
```

Disabling the worker prevents email delivery; queued email jobs remain pending.
Do not enable this profile as a way to bypass missing credentials, and do not
paste real Resend keys into diagnostics or Git.

### Operations API and optional email delivery

`GET /api/operations/workers` is admin-only and reports durable heartbeat
observations, not Docker container state. All four core workers (`vector`,
`crawler`, `lineage_invalidation`, `alert_delivery`) are always expected.
Email delivery is optional. With `SDE_EMAIL_WORKER_EXPECTED=false` (the
Compose default), an email worker with no heartbeat in the five-minute
concern window is reported as `disabled` rather than `unavailable`.

The response includes `expected` for each worker kind. A running unexpected
email worker is still reported as `healthy` or `degraded`; a recently stale
email worker is still `unavailable` or `degraded` until the five-minute
window expires. This avoids hiding real worker incidents during a disable or
migration. A disabled email worker does not mean queued email was delivered.

When enabling the email Compose profile, set `SDE_EMAIL_WORKER_EXPECTED=true`
in the API deployment environment and recreate the API to apply the changed
expectation. Set it back to `false` when disabling email. Only the explicit
strings `true` and `false` are accepted. Do not use the API `/health` or
`/ready` endpoints as substitutes for the worker operational overview:
`/health` is liveness, and `/ready` checks only PostgreSQL and Redis.

### Vector pipeline capability inventory

`GET /api/operations/capabilities` is restricted to authenticated admins. It
reports the five stages controlled by the Compose vector worker switches:
embedding generation, vector analysis, risk scoring, pattern detection, and
anomaly detection. The response uses the API's deployment flags and current
vector worker heartbeat status. In the supplied Compose file, the API and
vector worker receive the same five flags. External deployments must keep
them in sync, or the inventory will report the API's intent rather than the
worker's actual launch arguments.

Each stage reports `requested` and one of `disabled`, `misconfigured`,
`worker_unavailable`, `worker_degraded`, or `configured`. `configured` means
its flag is enabled, local prerequisites checked by this inventory are present,
and a fresh healthy vector worker heartbeat exists. It **does not** prove that
work was processed, remote provider quota is available, or a particular
snapshot is caught up. An invalid toggle is treated as misconfigured. If
embedding generation is enabled, a supported `EMBEDDING_PROVIDER` and its
corresponding API key must be present; neither credential values nor a provider
endpoint URL are returned. The inventory never calls an external provider.

`/health` remains an API liveness probe, while `/ready` only checks PostgreSQL
and Redis. Do not use either endpoint as an assertion that semantic embeddings
are being generated. Embedding generation remains disabled by default, and
`disabled` is intentional in that configuration. The inventory does not cover
independent crawler, alert, or invalidation execution capabilities, or pipeline
stages without explicit Compose switches.

## 4. Runtime Capabilities

### Continuous crawler scheduler

The continuous crawler scheduler runs:

    python -m scripts.run_crawler

The scheduler continuously claims due `MonitoredPage` records from PostgreSQL and dispatches each claimed page through the source-specific crawler registry.

A source with no registered crawler adapter is a failed page crawl, not a fatal
scheduler error. Its claim is finalized with the existing retry policy, an
operational cycle failure is recorded, and the scheduler continues processing
other due pages. Unexpected database or claim-finalization errors still cause
the process to fail rather than silently discard work.

Each claim is committed before external crawler work begins. Crawler execution therefore runs without a scheduler-owned database transaction. Success or failure is finalized in a fresh transaction fenced by the durable claim token.

The runtime processes one claimed page at a time per scheduler process. Multiple scheduler processes can safely compete for work because claiming uses PostgreSQL row locking with `FOR UPDATE SKIP LOCKED`.

`CRAWLER_SCHEDULER_POLL_SECONDS` controls how long an idle scheduler waits before checking for due work again. The default is 5 seconds.

SIGINT and SIGTERM request graceful shutdown. The scheduler stops taking new work, allows the current crawl and its finalization to finish, and then exits.

If a scheduler process dies after claiming work but before finalization, the durable claim lease eventually expires and the page becomes eligible for recovery by another scheduler process.

Crawler side effects are at-least-once. A process failure after snapshot persistence but before successful scheduling finalization can cause the page to be crawled again after lease expiry. The runtime does not claim exactly-once execution.

### Lineage invalidation executor

The lineage invalidation executor runs:

```text
python -m scripts.run_lineage_invalidation_executor
```

It processes previously authorized invalidation work through the execution claim, fencing, target-resolution, downstream-invalidation, and immutable-result pipeline.

The executor does not authorize decisions. Execution authority must already exist before work becomes executable.

The base Compose topology declares the lineage invalidation executor as a long-running service with a restart policy.

The service consumes `SDE_DOWNSTREAM_DATABASE_URLS` for deployment-managed downstream connection resolution and `LINEAGE_INVALIDATION_EXECUTOR_POLL_SECONDS` for its polling interval.

The executor still consumes only previously authorized work. Deploying the executor does not grant execution authority.

### Operational heartbeat failure handling

The crawler scheduler and lineage invalidation executor publish durable
heartbeats independently of their work cycles. If heartbeat persistence fails,
the heartbeat runner retains the error and requests cooperative shutdown of
the corresponding worker. The current cycle may finish, but the worker stops
claiming new work; the process then exits with an error so its supervisor can
restart it. This prevents an indefinitely running worker from continuing
without operational reporting.

The heartbeat callback is not an execution cancellation or rollback mechanism.
Existing claim leases, fencing and idempotency rules govern recovery after a
process failure. Other worker implementations retain their own heartbeat and
failure-handling policies.

## 5. Production Override

`docker-compose.prod.yml` changes the production boundary by:

- setting production environment behavior,
- removing direct host exposure of the API,
- placing services on a backend network,
- adding Caddy,
- exposing ports 80 and 443 through Caddy,
- enabling production restart policies.

Use the base and production files together according to Docker Compose override semantics.

## 6. Reverse Proxy

Caddy is the production ingress boundary.

The reverse proxy uses:

```text
/ready
```

for upstream readiness.

This is intentionally different from the API container liveness probe.

## 7. Liveness and Readiness

### `/health`

`GET /health` is the liveness probe.

It confirms that the API process is alive and deliberately avoids dependency checks.

Temporary PostgreSQL or Redis failure should not by itself cause an otherwise live API process to be restarted.

### `/ready`

`GET /ready` is the readiness probe.

It should be used by traffic-routing infrastructure that needs to know whether dependency-backed requests can be served.

The intended operational distinction is:

```text
process restart decision -> /health
traffic routing decision  -> /ready
```

## 8. Environment Configuration

Development configuration starts from:

```text
.env.example
```

Production configuration starts from:

```text
.env.production.example
```

Never commit production secrets.

At minimum, review configuration for:

- application secret key,
- PostgreSQL,
- Redis,
- cookie security,
- trusted proxy behavior,
- OAuth credentials if enabled,
- email provider credentials,
- DeepSeek semantic analysis,
- embedding provider,
- downstream execution database references.

## 9. Embedding Providers

The embedding provider is selected with:

```text
EMBEDDING_PROVIDER
```

Supported configured provider paths include:

```text
openai
gemini
```

### OpenAI-compatible

Relevant settings include:

```text
OPENAI_API_KEY
OPENAI_EMBEDDING_BASE_URL
OPENAI_EMBEDDING_MODEL
OPENAI_EMBEDDING_DIMENSIONS
```

An empty base URL uses the provider's default OpenAI endpoint behavior.

A compatible base URL can be supplied for an OpenAI-compatible embedding service.

### Gemini

Relevant settings include:

```text
GEMINI_API_KEY
GEMINI_EMBEDDING_MODEL
```

Provider credentials must be supplied through deployment secrets rather than committed configuration.

## 10. Downstream Execution Connections

Physical downstream execution can resolve connection references through:

```text
SDE_DOWNSTREAM_DATABASE_URLS
```

This value maps non-secret binding references to runtime database connection URLs.

Execution bindings should identify connection references rather than persist raw database credentials as lineage metadata.

Supply real connection values through the deployment secret-management mechanism.

## 11. Database Migrations

SDE uses ordered SQL migrations under:

```text
scripts/migrations/
```

The migration runner is:

```text
python -m scripts.migrate
```

The migration system records applied migrations and executes forward migrations in deterministic filename order.

Do not introduce a second migration framework without an explicit architecture decision.

## 12. Persistent Data

The Compose topology uses persistent volumes for:

- PostgreSQL data,
- Redis data,
- backups,
- snapshot data,
- Caddy state in production.

Backup and restore scripts exist under `scripts/`.

Production operators should validate backup restoration, not merely backup creation.

## 13. Production Startup

A production deployment should provide a production environment file and combine the base and production Compose definitions.

Before accepting traffic:

1. validate environment configuration,
2. start PostgreSQL and Redis,
3. apply database migrations,
4. start application and deployed workers,
5. verify `/health`,
6. verify `/ready`,
7. verify Caddy/TLS behavior,
8. verify worker connectivity,
9. verify backup configuration.

Exact deployment commands should be adapted to the deployment environment and secret-management system rather than embedding secrets into shell history.

## 14. Operational Gaps Before Production Freeze

The following runtime work remains before the repository should be presented as a complete autonomous production system:

- validate the deployed worker and scheduler topology under production operating conditions,
- add stronger application and worker metrics,
- run full cross-browser and device compatibility validation.

## 15. Observability Targets

Production observability should expose more than process health.

Important operational and product metrics include:

- lineage coverage,
- change detection latency,
- impact resolution latency,
- decision-to-execution latency,
- stale context exposure window,
- avoided reprocessing units,
- execution failure rate,
- queue depth,
- dead-letter queue size,
- provider error rate.

These metrics connect infrastructure health to the product's actual knowledge-integrity value.

## 16. Security Gate

The production-readiness security review covers the following boundaries:

- authentication and sessions,
- CSRF,
- API-key lifecycle and permissions,
- secret handling and rotation,
- tenant isolation,
- rate limiting and abuse protection,
- security headers,
- dependency and supply-chain exposure,
- container and network boundaries,
- database roles,
- OAuth,
- crawler/SSRF boundaries,
- logs and sensitive-data handling,
- worker authorization,
- execution replay and fencing behavior,
- data retention and deletion.

Do not treat successful functional tests as a substitute for this security gate.