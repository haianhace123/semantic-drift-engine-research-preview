# Semantic Drift Engine

Semantic Drift Engine (SDE) is an experimental control plane for tracking changes in knowledge used by retrieval-augmented generation (RAG) systems and AI agents. It records source revisions, evaluates material changes, and uses recorded dependency lineage to identify downstream knowledge that may need review or invalidation.

The project is published as a research preview. It is not a managed service, a production-certified safety system, or a guarantee that an AI application will always use current information.

## The problem

Reindexing a changed document is not the same as knowing which downstream artifacts and applications relied on its previous revision. SDE explores that distinction with versioned source evidence and explicit consumption lineage.

An SDE decision follows this sequence:

1. Observe a source and persist a revision or crawl outcome.
2. Compare revisions and evaluate whether a change is material.
3. Follow recorded lineage to identify affected downstream revisions.
4. Record an impact or invalidation decision, with evidence and uncertainty.
5. Require separate authorization before performing a downstream operation.
6. Record the operation and verify recovery independently.

SDE does not invent exact dependencies from similarity scores. If an external consumer has not recorded sufficient lineage, SDE cannot establish that consumer's exposure with certainty. A successful worker cycle or an empty set of affected revisions is not evidence that all downstream applications are protected.

## Current capabilities

| Area | Implementation | Important boundary |
| --- | --- | --- |
| Source monitoring | Public HTML crawling, scheduling, snapshots, crawl outcomes | Availability and coverage depend on source access and configured workers |
| Semantic assessment | Revision comparison, embeddings, materiality and risk evidence | Provider access and independent accuracy validation are separate requirements |
| Temporal evidence | Historical revisions, events, topology and investigation views | Evidence does not imply a complete record of every external change |
| Dependency lineage | Tenant-scoped lineage observations, dependency traversal and causal paths | Exact lineage must be supplied by an instrumented consumer |
| Invalidation | Decisions, policy activation, operator authorization, fenced execution and verification | Execution is not automatic merely because a change was detected |
| Operations | Authentication, role checks, API keys, worker diagnostics, alerts and backups | Production deployment and tenant-wide data lifecycle remain operator responsibilities |

The UI includes source monitoring, change investigation and historical views. Its presence does not imply that every workflow has passed browser or customer acceptance testing.

## Architecture

The reference Docker Compose deployment includes a FastAPI application, PostgreSQL with pgvector, Redis, a crawler scheduler, vector intelligence worker, alert delivery worker and a lineage invalidation executor. A transactional email worker is available as an opt-in profile.

The frontend uses React, TypeScript and Vite. Production ingress is intended to run behind Caddy with explicitly configured trusted proxy addresses. Do not publish the development API directly to the internet.

Start with [Architecture](docs/architecture.md), [Deployment](docs/deployment.md) and [API reference](docs/api_reference.md). For external RAG adoption, read [Lineage integration](docs/lineage_integration.md) and [LangChain context admission](docs/langchain_context_admission.md).

## Local setup

### Prerequisites

- Docker Engine and Docker Compose v2.
- A Git checkout or a verified copy of this release.
- Sufficient local disk space for PostgreSQL, images and snapshot storage.
- Provider credentials only for optional AI and email integrations you choose to enable.

This is a local development workflow, not an internet-facing production deployment guide.

```sh
cp .env.example .env
```

Review all credentials, endpoints and storage settings in `.env` before starting. Replace sample secrets. Do not commit `.env`, API keys, tokens or local data.

```sh
docker compose config --quiet
docker compose up -d
docker compose run --rm api python -m scripts.migrate
```

Check the application liveness and dependency readiness endpoints on the API port configured by the Compose file:

```text
GET /health
GET /ready
```

`/health` checks the API process. `/ready` checks PostgreSQL and Redis; it does not prove that semantic providers, email delivery or downstream admission controls are working. Use [Deployment](docs/deployment.md) for worker expectations, stage configuration, production overrides and troubleshooting.

A default local deployment may run without external embedding or semantic provider credentials, but the corresponding analysis stages will not produce provider-backed results. Email-based verification and password recovery require a configured transactional email worker. Do not treat a running login page as proof that public registration works.

### Running tests

From a suitable Python environment:

```sh
python -m pytest -q tests/unit
```

The frontend also has JavaScript tests and a production build in `frontend/`. Database-backed and downstream integration tests require a disposable test environment and their documented opt-in gates. Do not run integration, restore or invalidation tests against a live pilot or customer database.

See [Isolated integration testing](docs/isolated_integration_testing.md) and [Acceptance governance](docs/test_acceptance_governance_v1.md).

## External integrations and control boundaries

SDE exposes tenant-scoped lineage APIs for observations, impact analysis and invalidation decisions. API-key access is governed by permissions and organization boundaries. See [API reference](docs/api_reference.md) for the supported endpoints and [Lineage integration](docs/lineage_integration.md) for the instrumentation contract.

Registering a lineage observation or finding a material source change does not authorize a destructive operation. Execution authorization is a separate operator action. Supported operators can inspect execution target identity, issue authority through `scripts.lineage_authority`, and allow the executor to consume previously authorized work. The physical target must be verified independently. Misidentified, missing or stale lineage must not be silently reconstructed or treated as safe.

An experimental external PGVector smoke example was withheld from this public snapshot during release security review. Its associated documentation, bootstrap script and tests are also excluded to avoid shipping broken instructions. This does not affect the core LangChain/PGVector integration modules included in `src/integrations/`.

## Security and data protection

Run this software only with data you have authority to collect, process and retain. Source snapshots, event evidence, provenance, account records and operational logs can contain personal or confidential information. Optional AI providers, email services and downstream database connections introduce additional data flows.

Self-hosted operators are responsible for access policies, secure configuration, backups, retention, incident response, lawful processing and the Terms and Privacy notice presented to users. The HTML legal pages in `src/static/` are deployment templates, not terms issued by a central hosted SDE service. They must be completed and reviewed before enabling public accounts. See [Deployment legal review](docs/legal_deployment_review.md).

Account deactivation is not organization-wide deletion. Consult [Account and organization data lifecycle](docs/account_deactivation_data_scope_20261011.md) before making any deletion, export or compliance claims.

See [Security policy](SECURITY.md) to report vulnerabilities. Do not disclose tokens or production secrets in public issues.

## Release status

Version `v0.1.0` is a source-only Research Preview. It is not a production certification and the maintainers do not operate a public hosted SDE service.

The reviewed RC2 source passed checksum verification of 869 files, an independent Docker image build, startup against a disposable PostgreSQL and Redis environment, application of 50 database migrations and seven HTTP acceptance cases. The environment was cleaned up after verification. These results are recorded in [Release validation](docs/release_validation_20261017.md). Separate Python and JavaScript regressions also passed. None of these checks establish semantic assessment accuracy or full customer-facing workflows.

Unverified areas include full browser E2E, transactional email delivery, optional AI providers, load characteristics and internet-facing deployment. This source release does not certify ownership of third-party trademarks or authorize public registration. Refer to the publication checklist and asset notice before redistribution or deployment. Terms and Privacy are templates for operators, not policies for a hosted service. See [Publication review](docs/publication_review.md) and [Release notes](RELEASE_NOTES.md).

## Repository documentation

- [Architecture](docs/architecture.md): components, authority boundaries and evidence model.
- [Deployment](docs/deployment.md): runtime services, configuration and operating limits.
- [API reference](docs/api_reference.md): supported HTTP integration contracts.
- [Lineage integration](docs/lineage_integration.md): consumer instrumentation and downstream controls.
- [Semantic holdout evaluation](docs/semantic_holdout_evaluation.md): evaluation methodology and limitations.
- [Release notes](RELEASE_NOTES.md): the specific scope of this preview.
- [Release validation](docs/release_validation_20261017.md): verified RC2 runtime and remaining acceptance limits.
- [Publication review](docs/publication_review.md): license, asset and privacy decisions before public release.
- [Security policy](SECURITY.md): responsible vulnerability reporting.

## License

Original SDE software and documentation are released under the [MIT License](LICENSE), to the extent the copyright holder has authority to license them. This does not license third-party trademarks or separately licensed components. See [Brand and asset notice](BRAND_NOTICE.md) and [Asset provenance and third-party marks](docs/asset_provenance_review.md).

## Source distribution security

This open-source snapshot excludes the historical GitHub Actions workflow after a generic-credential warning in that file. No private Git history is distributed; this is a clean, source-only release commit. CI should be re-established from an independently reviewed workflow before automated builds are relied upon. See [Open-source security notes](docs/open_source_security_notes.md).
