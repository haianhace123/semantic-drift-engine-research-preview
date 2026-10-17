# LangChain physical readback before lineage publication

**Scope:** strict-by-default verification for the SDE-owned LangChain/PGVector
writer integration. No DB migration or change to the HTTP admission protocol.
This is not a full lineage coverage or event-freshness certificate.

## Problem

`add_documents(..., ids=...)` returning the requested IDs does not prove
that every persisted row contains the submitted text and provenance. The
in-process and HTTP indexing integrations previously published chunk and index
materialization observations after the ID ACK alone. A writer or storage
adapter that changed a chunk while returning the expected IDs could therefore
produce valid-looking but false lineage.

## Contract

Both `index_langchain_documents_with_lineage()` and
`index_langchain_documents_with_remote_lineage()` default to
`require_physical_readback=True` for new calls. Existing caller code that
omits the flag now requires a `get_by_ids`-capable, collection-bound reader.

1. Reject a vector store without a callable `get_by_ids` before physical I/O.
2. Prepare canonical IDs and content-addressed revisions. Capture the exact
   UTF-8 text and source/snapshot/revision metadata **before** calling the writer.
3. Call `add_documents` and validate the exact ID ACK using the existing gate.
4. Call `get_by_ids` on the *same collection-scoped vector store*. Require one
   returned row per expected ID, allowing any order but no missing, duplicate
   or extra IDs. Compare the submitted text and source/snapshot/chunk revision
   byte-for-byte/field-for-field with the physical readback.
5. Only after readback succeeds may the authority DB receive staged lineage
   observations or the authenticated HTTP publisher be called. The index
   observation remains last. Both returned result types expose
   `point_in_time_readback_verified`, which is false in legacy mode.

Readback failures are fail-closed for **publication**, but the downstream
physical write might already have happened. Stop and reconcile that collection
before retrying. A failure does not imply an automatic rollback across stores.

## Integration usage

```python
result = index_langchain_documents_with_lineage(
    vector_store=pgvector,  # Bound to the correct tenant/collection
    authority_db=db,
    organization_id=org_id,
    source_external_id="policy-source",
    documents=documents,
    index_external_id="policy-index",
    index_revision_external_id="policy-index-r7",
    sequence=7,
    require_physical_readback=True,
)
assert result.point_in_time_readback_verified
# The caller still owns authority_db.commit() and reconciliation.
```

**Boundary of confidence:** `get_by_ids` must actually read the authorized
physical collection rather than echo or return an unchecked cache. The caller
must independently bind the collection to the correct organization and source;
this helper has no credential or collection-registry authority. A successful
readback is point-in-time only, is not an atomic multi-store commit, and does
not show that the publisher knew every upstream dependency or that all later
semantic events have been processed. It grants **no model-admission permission**.

Legacy ID-ACK-only behavior remains available only through an explicit
`require_physical_readback=False` opt-out. Never represent opt-out publication
as physically verified or silently promote `not_affected` to globally safe.
This is a source-level change: deployed images and other integration stacks
retain their existing behavior until separately accepted and rolled out.

## Acceptance

The unit regression suite includes successful out-of-order reads, Unicode,
missing rows, duplicate IDs, altered policy text, source/snapshot/revision
mismatches, writer mutation, readback exceptions and unsupported readers for
both local and HTTP publication. A live isolated PGVector acceptance of strict readback on the P2.8B source
passed on 2026-10-16 (P2.8C), with one real-DB integration and 32 strict
regressions. This verifies the tested integration, not every possible
LangChain version, multi-tenant collection selector, or production deployment.
