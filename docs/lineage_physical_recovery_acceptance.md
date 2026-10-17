# PGVector recovery acceptance boundary

## Supported execution and evidence contract

SDE can invalidate a causally pinned LangChain PGVector chunk and read the
downstream database after commit to confirm that the old revision is absent at
that physical identity. The execution worker does not rebuild the index.

A downstream integration is responsible for rebuilding the artifact, inspecting
its own physical output, and then publishing a new materialization observation.
The observation contains exact dependencies, a new asset revision and a
monotonically advanced subject sequence. The integration can then bind that
observation to the successful invalidation execution result.

The Control Plane projects:

- `awaiting_evidence` after invalidation and after any unreported physical
  rebuild. Neither execution success nor a new PGVector row independently
  establishes a new recorded materialization.
- `stale_dependency_present` when the bound current materialization still
  reports the causally invalidated revision, even when a replacement chunk is
  physically present.
- `verified` only for the **specific recorded lineage predicate**: a current,
  post-execution materialization of a newer target revision no longer reports
  that exact stale direct dependency.

The word `verified` is not independent proof that the physical index is complete,
that the producer faithfully reported every dependency, that all queries are
using the rebuilt artifact, or that a later write cannot reintroduce stale data.
The producer's attestation is a trust boundary. A distinct, integration-specific
physical repair attestation is required before SDE can offer a stronger recovery
guarantee; the current API must not be presented as that guarantee.

## Integration qualification

`tests/integration/test_lineage_physical_recovery_acceptance.py` exercises the
real authority and downstream PostgreSQL databases, using a separate producer
write to create the replacement PGVector revision. It checks actual physical
rows, terminal execution, required evidence, tenant isolation, idempotent
binding and worker retry. Its two cases differ only in whether the recorded
materialization retains the exact stale dependency. Both must keep their own
integrity outcome despite identical physical replacement.

Run this test only with the dedicated `integration_downstream_db`, never against
production data. This is an acceptance test for the supported boundary, not a
production physical repair adapter.

## Admission semantics for repaired revisions

The integrity projection's `verified` status belongs to the original
invalidation **target revision**. It establishes that the replacement
materialization has been attested against the invalidated dependency; it
does not revalidate the old target's bytes. LangChain admission must still
reject that historical revision (`superseded_revision`). A replacement
revision needs its own independent `not_affected` decision, rather than
inheriting the old target's verification status.

## Isolated persisted-admission qualification

Run the recovery acceptance cases only with disposable authority and downstream
PostgreSQL databases, Redis, and an HTTP API using the current reviewed source.
Do not rebuild, retag or replace the shared `semantic_drift_engine-api:latest`
image to qualify these tests. Use a separate Compose project with cached images,
a read-only source mount, and an isolated database identity check before migrations.
Require both physical recovery cases and the event-to-HTTP admission case to pass
without skips.

The test uses `SDEIntegrityClient` against the isolated HTTP API and applies
LangChain admission to documents reconstructed from actual downstream PGVector
rows. An unknown revision is blocked; pending invalidation blocks as
`unresolved_impact`; a successfully attested recovery keeps the historical
target blocked as `superseded_revision`. A rebuild reporting the stale
dependency remains `affected` and blocked. Requests from another organization
must not inherit the owner's integrity decisions.

In the clean-replacement case, separately persisted `no_action` decisions for
the successor index and chunk allow the owner to admit the replacement. Those
decisions are controlled test inputs, not evidence that the replacement's
physical contents or semantic correctness were independently verified. The
HTTP qualification establishes request-time contract behavior, not a complete
physical recovery guarantee or protection against concurrent downstream writes.
