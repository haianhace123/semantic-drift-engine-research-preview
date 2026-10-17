# PGVector physical invalidation confirmation

The lineage invalidation executor operates on an exact causal chunk revision in
one authorized PGVector collection. A successful SQL DELETE/commit by itself
is not sufficient evidence for reporting terminal execution `succeeded`.

After committing the downstream transaction, the executor now starts another
transaction and checks collection tenant/source/index provenance and the
physical document row. It can report execution `succeeded` only when the
pinned source revision is no longer present at that document identity. A
replaced revision for the same source is not deleted and is not treated as
proof of rebuild; only the pinned old revision's absence is confirmed.

A definitive stale-row or provenance mismatch records an execution failure;
connection/read-back errors do not publish success and follow existing lease
reclaim and retry handling. The readback locks are released promptly.

This is **physical invalidation confirmation**, not a repair attestation. It
cannot prove a new materialization was physically rebuilt, that an application
stopped caching stale content, or that an external system remains clean after
the point-in-time read. The distinct lineage verification and Target Integrity
contracts still require fresh, independently attested post-repair evidence.

No new state authority, new database tables or migration is introduced.
Tests exercise failure when deletion falsely reports success and the chunk is
still present, plus tenant/provenance isolation and legitimate replacement.
