# Joined LangChain SDK → event-driven HTTP admission: acceptance candidate

**Status:** candidate for isolated Windows/Docker execution, **not** production acceptance, not deployed. The local development environment used to author this kit has no Docker or `langchain-postgres`; only Python syntax and fail-closed gate unit tests can be run locally. The physical SDK/PGVector/HTTP test must pass **1/1, zero skipped** on the user's disposable Docker project before this batch may close.

## Why this exists

The repository already has separate acceptance for (a) classified semantic event -> persisted invalidation -> HTTP refusal, and (b) real LangChain SDK round trips against PGVector. Those were not a single connected consumer action. The new `test_joined_langchain_http_admission.py` combines the two without introducing a second serving policy authority.

## What the scenario actually asserts

1. Two real test snapshots contain 30-day and 7-day refund policies, with SHA-256 and correlation from the historical snapshot to materialized chunk/index provenance.
2. Real `langchain_postgres.PGVector` stores a LangChain `Document` with `source_external_id`, `snapshot_external_id` and exact `chunk_revision_external_id` in a randomly named, owner-scoped PGVector collection.
3. Real SDK similarity search retrieves the old bytes and revision. With no decision, the tenant-scoped SDE HTTP client blocks `unknown_integrity` and never calls the mock model.
4. A controlled, completed classification event is persisted. The real temporal invalidation worker creates exactly one invalidate decision for the causal target, associated with the event provenance row.
5. The same real SDK still retrieves stale bytes from PGVector (no physical deletion was authorized). The owner HTTP admission blocks `unresolved_impact`; a second organization's HTTP API key sees `unknown_integrity`, not the owner's private evidence. No model callback is invoked.
6. Re-running the temporal worker makes no additional decision.

The test uses a fake model callback to prove it was **not** invoked, not an external LLM. It neither measures autonomous semantic classification quality nor repairs an index. Full detect -> deny -> authorized remediation -> **attested** recovery with real SDK is a separate milestone.

## Resource boundaries

- Compose project: `sdejoined<12 random hex>`, created for one run.
- Candidate API/tests image: `sde-joined-<random>:local`; does **not** retag `semantic_drift_engine-api:latest`.
- PostgreSQL: disposable `sde_integration` and `downstream_test` only; disposable Redis and HTTP API on internal Docker network only.
- No host ports, bind mounts or privileged services. The runner checks resolved Compose config before building.
- Python optional SDK dependencies are installed only in a temporary venv in the disposable tests container.
- Teardown uses `docker compose -p <random-owned-name> down --volumes --remove-orphans` **only** for that unique project. Never substitute a production project name or run plain `docker compose down -v`.
- The original `.dockerignore` excludes `.env` and runtime credentials from image build context. Confirm this remains true.

## Acceptance and exit criteria

- Static gate tests: `13 passed` for environment identity, missing/skipped/failing report refusal.
- Disposable Docker run: `SDE_JOINED_LANGCHAIN_HTTP_ACCEPTANCE_PASSED: 1 passed, 0 skipped` followed by `SDE_JOINED_ACCEPTANCE_VERIFIED`.
- Any missing Python dependency, unable-to-build image, wrong DB identity, stale SDK schema, incorrect worker result, tenant leak, or missed block **fails** the run. Do not silently skip or switch to production infrastructure.
- No commit until complete logs are reviewed. No push/deploy on the basis of this isolated acceptance.
- Verify all containers and anonymous/named volumes associated with the random project are cleaned up; other projects remain untouched.

## Run

In repository root after applying the candidate:

```powershell
python -m pytest -q tests/unit/test_ci_joined_langchain_http_gate.py
if ($LASTEXITCODE -ne 0) { throw 'Static gate contract failed' }

& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/run_isolated_joined_langchain_http.ps1
if ($LASTEXITCODE -ne 0) { throw 'Joined isolated acceptance failed' }
```

The Docker test intentionally downloads pinned optional LangChain packages and pinned base images if not cached. It does not call commercial model APIs or process a live customer source.

## Next proof after success

Join an authorized physical invalidation/consumer rematerialization to **new provenance attestation** and HTTP admission of a clean successor revision. Add a source-to-context quality benchmark measuring false blocks, false allows, time to deny and time to verified recovery; produce a customer-credible before/after incident report. Do not claim this stage proves commercial value by itself.
