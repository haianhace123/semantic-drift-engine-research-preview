# PGVector collection identity for causal invalidation

`langchain_pgvector_causal_chunk` authorizes one chunk revision on a recorded
lineage path. A chunk can be indexed into multiple PGVector collections, so
chunk ID and revision metadata alone cannot authorize the physical deletion.

A supported PGVector collection must have metadata identifying the SDE tenant,
source, and logical index. The trusted index writer supplies this identity when
creating the collection:

```python
from src.integrations.langchain import sde_pgvector_collection_metadata
from langchain_postgres import PGVector

store = PGVector(
    embeddings=embeddings,
    connection=postgres_dsn,
    collection_name=collection_name,
    collection_metadata=sde_pgvector_collection_metadata(
        organization_id=organization_id,
        source_external_id="source-a",
        index_external_id="index-a",
    ),
    use_jsonb=True,
)
```

The metadata fields are `sde_organization_id`, `sde_source_external_id`, and
`sde_index_external_id`. They describe the collection, not an individual chunk
revision. A collection dedicated to a different index or tenant cannot serve
as the execution target for this decision.

The terminal index dependency must be recorded on an index materialization;
a consumption observation alone is not physical indexing evidence.

The execution binding retains only `connection_ref` and `collection_name`.
The executor resolves the chunk from the authorized lineage path, locks and
checks collection identity, then locks and checks the chunk's source/revision
metadata. Identity is checked even if the chunk is absent, before granting an
idempotent `deleted=False` outcome. Both locks are held until the caller
commits or rolls back.

## Migration and trust boundary

Collections without these tags fail closed. The integration owner must verify
the actual collection-to-index ownership out of band before provisioning tags
on an existing collection. Never fill in missing metadata simply by copying
an execution binding: that would recreate the original misbinding flaw.

Collection metadata is an attestation by the trusted materializer, not a
cryptographic proof. Downstream roles that can mutate collection metadata can
also forge the claimed identity; restrict that privilege independently of SDE
execution permissions. A single physical collection shared between distinct
SDE index identities is outside this target kind's contract.

This change does not make one-chunk execution a fan-out repair, and it does
not by itself mark downstream index integrity as verified.
