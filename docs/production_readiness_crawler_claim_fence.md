# PR.4 Scheduled crawler snapshot claim fencing

A crawler lease previously governed only scheduler finalization. The crawler
persisted a snapshot (and then invoked drift detection) in a separate database
transaction before the scheduler checked whether its claim was still current.
An expired or reclaimed worker could therefore write evidence, even though it
could no longer finalize that claim.

The scheduled crawler now passes the claim token all the way to snapshot
persistence. Before inserting a snapshot, persistence locks the exact
organization-scoped monitored-page row and checks token equality and lease
expiry against PostgreSQL server time. The lock is held until the snapshot
transaction commits. Reclaim and snapshot persistence serialize on the same
row. A manually invoked crawler can still create a page/snapshot as before;
when targeting an existing page, it cannot bypass an active scheduled claim.

The normal failure/lease-loss path also avoids terminating the scheduler:
claim authority errors during finalization mean a different worker may now
own the job, not that the old worker may reset the claim.

This is a database evidence-write fence, not exactly-once crawling. Network
fetches may overlap; a snapshot committed under a valid claim can still be
followed by a later retry if finalization fails. The filesystem write occurs
before database insertion; the existing crawler cleanup path removes the
unreferenced file when the database fence rejects the snapshot.

Integration tests exercise expired tokens, reclaimed tokens, valid new tokens,
manual bypass attempts, and cross-tenant page identities. No schema change is
needed; this uses the existing crawl claim columns and row locks.
