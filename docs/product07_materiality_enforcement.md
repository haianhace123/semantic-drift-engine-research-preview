# Product 07: materiality before downstream invalidation

The temporal invalidation service must not interpret every changed HTML or text
hash as a change to the knowledge used by a consumer. A persisted, completed
semantic event with a coherent `changes_count = 0` is an equivalent edit:
no downstream invalidation decisions or provenance are created, even when an
exact snapshot-to-lineage correlation exists.

For `changes_count > 0`, the normal tenant-scoped old-snapshot correlation and
impact evaluation continue unchanged. A malformed or contradictory completed
semantic payload raises `TemporalLineageInvalidationEvidenceConflictError`
before any lineage decision is written; the vector worker handles this via
its existing failure/retry policy. Both the current nested
`semantic_analysis.changes` representation and historical top-level `changes`
representation are supported. There is no inference from hash inequality.

## Live-pilot evidence boundary

The public source pilot currently has four hash-verified snapshots and two
completed semantic events, but no exact snapshot-lineage correlations. A
completed temporal invalidation with zero roots is a successful worker cycle,
not evidence that a downstream system was protected. Do not backfill a
correlation just to attribute already completed decisions retroactively. A
consumer must first materialize actual source-snapshot bytes into its separate
vector index, publish immutable lineage observations for those revisions,
and associate the exact crawled snapshot with that recorded materialization.
Then a *future* material source change is eligible for event-to-consumer
invalidation. Verify both a material change and an equivalent rewrite, with
false blocks and missing coverage counted explicitly.

This batch changes no production database rows, source snapshots, consumer
state, or configured providers. It is not a live consumer acceptance result.

## Verified acceptance boundary (2026-10-13)

Source verification on Git HEAD `7b28bca` passed 72 unit and admission-protocol
checks. A separate, disposable PostgreSQL/Redis/HTTP acceptance environment
mounted the reviewed source read-only and completed six integration cases:

- Two PostgreSQL materiality cases (material change and equivalent rewrite).
- One vector-worker decision/provenance persistence case, with no skip.
- Three persisted lineage/event-to-HTTP downstream admission cases.

All six cases passed with zero skips. The disposable services and volumes were
removed. This is source/runtime contract evidence, not a deployed customer
acceptance or an independent benchmark of semantic detector quality.

The historical untracked `scripts/run_isolated_lineage_admission.ps1` expects
an API image rebuilt from the working tree. That runner is **not** the verified
acceptance method and must not be used on the present Docker environment.
The accepted procedure used locally available images, no builds or pulls,
read-only source mounts, a unique internal-network Compose project, explicit
database identity checks and teardown confined to that project. Future
acceptance runs must retain those isolation controls.
