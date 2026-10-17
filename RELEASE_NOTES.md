# Semantic Drift Engine v0.1.0

Status: source-only Research Preview. Not a hosted service or production certification.

This release contains an experimental knowledge-integrity control plane for RAG and AI-agent integrations. Its scope includes revision evidence, semantic materiality assessment, registered downstream lineage, invalidation decisions, operator authorization and verification of selected recovery operations.

## What is included

- Source monitoring, historical snapshots and semantic change records.
- Tenant-scoped lineage, impact and invalidation APIs.
- Explicitly authorized downstream execution with target and revision checks.
- Operator-facing source, evidence and temporal investigation views.
- Docker Compose development topology and documented isolated acceptance workflows.

## What has been checked

The RC2 source snapshot passed 869-file SHA-256 verification and a local Docker image build. An isolated runtime test with new PostgreSQL and Redis resources applied 50 migrations, started the API and passed seven HTTP acceptance cases; the created resources were removed. The runtime test did not expose host ports or target existing application data. Prior S16 source regressions passed (20 Python tests and six JavaScript tests), and a separate Redis 7 rate-limiter test passed 40 concurrent attempts with five allowed and 35 denied.

These are bounded tests, not evidence of complete security, browser usability, email deliverability or semantic detection accuracy. Exact results and limitations are listed in `docs/release_validation_20261017.md`.

## Known limits

- External consumer lineage must be instrumented; it cannot be reconstructed solely from semantic similarity.
- Semantic accuracy has not been independently established on a representative customer corpus.
- Public registration depends on email and authentication capabilities that must be configured and verified.
- Source-only publishing does not constitute a hosted service.
- An external PGVector smoke-test example and its associated legacy helper files have been withheld pending separate security review. Core supported integration modules remain in the repository.
- The Terms and Privacy pages are operator templates; publishing this source does not authorize public customer enrollment.
- A source publication does not establish service-level data-processing compliance, trademark permissions, full browser acceptance, or production readiness. Clean-install runtime acceptance has passed for the seven tested HTTP cases only.

See `README.md`, `docs/deployment.md`, `docs/lineage_integration.md`, `SECURITY.md` and `docs/legal_deployment_review.md` for further details.
