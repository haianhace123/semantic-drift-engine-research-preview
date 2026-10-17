# External LangChain consumer admission: source closure

Status: source-level consolidation qualified by isolated acceptance on 2026-10-13. Not a production deployment approval.

## Scope

The executable external LangChain consumer passes retrieved documents through `admit_langchain_context` before consumer use. Unknown integrity is blocked by default. An explicit `--allow-unknown-for-lineage-proof` option is retained only for diagnostic lineage experiments without model generation; it is not a customer-serving policy. Existing `src.integrations.langchain` and `src.integrations.integrity_admission` remain the single admission authority in the integration layer. This batch does not introduce another policy engine.

Source validation covers the consumer entry point, revision provenance of indexed/retrieved PGVector documents, request-time checks, handling of superseded revisions, and the opt-out from the Redis rate-limit cleanup fixture for isolated PGVector tests. The latter avoids contacting a separate SDE Redis instance merely to run physical vector-store acceptance.

## Evidence and limits

- Source preflight: 17 reviewed files; 46/46 required unit/protocol checks on Windows (2026-10-13).
- Isolated real LangChain SDK and PGVector: 15/15 acceptance tests, zero skips; disposable database, random loopback-bound port, teardown verified (2026-10-13).
- Product 07 event-to-authority-HTTP/downstream lineage acceptance previously passed 6/6 on isolated PostgreSQL. This is separate evidence from the real LangChain SDK test, not one fully coupled authority-to-SDK scenario.
- The physical SDK acceptance uses controlled integrity evidence. It does not prove quality of automatic semantic classification, authorization of a real customer tenant, or successful live recovery across deployed systems.

## Deferred legacy harness

The existing `experiments/docker-compose.external-langchain-rag.yml` exposes a fixed development port and uses a hard-coded example password. The unmerged `scripts/ci_external_langchain_admission_pgvector.py` and its associated isolation test are coupled to that legacy harness. Do not treat this legacy harness as an approved isolated acceptance runner or commit it as part of this closure. The validated external Product 10 acceptance kit v3 uses a disposable Docker project, a random credential, strict loopback-only port verification, and removes only its own resources.

## Next production milestone

Run one isolated, joined acceptance with real SDE authority HTTP decisions and LangChain SDK retrieval from a fresh PGVector database, including independent tenant authorization, unknown/affected/verified decisions, and a successor revision with attested no-action evidence. The current test suite does not yet prove that complete joined execution. Replace or retire the legacy acceptance harness only after a safe, reproducible CI path exists. Verify model-side classification quality with a separately labelled semantic evaluation corpus.
