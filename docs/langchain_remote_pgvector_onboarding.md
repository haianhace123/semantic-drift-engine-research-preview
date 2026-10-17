# Remote LangChain/PGVector onboarding

This contract is for an external LangChain application that writes its own
PGVector collection and publishes lineage to SDE over HTTPS. It does not need
an SDE database connection or a database session. The Python code currently
lives in the SDE repository (`src.integrations`); it is not yet a separately
versioned, distributable SDK.

## Preconditions

- The SDE operator has provisioned the tenant and supplied an API key with the
  `write` permission. Use TLS and keep the key in a secret store.
- The operator has registered the downstream connection reference on the SDE
  server. Never pass PostgreSQL credentials in an execution target binding.
- The trusted PGVector collection writer provisions
  `sde_pgvector_collection_metadata(organization_id, source_external_id,
  index_external_id)`. The tenant UUID is provided by the SDE operator.
- A collection is dedicated to one tenant/source/index identity. The
  materializer's privileges to change collection identity are restricted.
- Document IDs are stable chunk IDs, and each document has
  `source_external_id` and `snapshot_external_id` metadata. The writer owns
  consistent, monotonic materialization sequences for each subject.

## Publish an index materialization

```python
import os
from langchain_core.documents import Document

from src.integrations.integrity_client import SDEIntegrityClient
from src.integrations.langchain.remote_onboarding import (
    index_langchain_documents_with_remote_lineage,
)

# `store` is a pre-provisioned LangChain PGVector store in the application.
# The collection is independently tagged with the SDE tenant/source/index.
documents = [
    Document(
        id="policy-refunds-1",
        page_content="Refund rules for this revision",
        metadata={
            "source_external_id": "policy-source",
            "snapshot_external_id": "policy-snapshot-17",
        },
    ),
]

with SDEIntegrityClient(
    base_url=os.environ["SDE_BASE_URL"],
    api_key=os.environ["SDE_API_KEY"],
    timeout_seconds=10,
) as lineage:
    published = index_langchain_documents_with_remote_lineage(
        vector_store=store,
        lineage_client=lineage,
        source_external_id="policy-source",
        documents=documents,
        index_external_id="policy-index",
        index_revision_external_id="policy-index-r17",
        sequence=17,
    )
    # The same API key identifies the tenant when publishing the binding.
    binding = lineage.publish_causal_pgvector_binding(
        source_external_id="policy-source",
        index_external_id="policy-index",
        binding_revision=1,
        connection_ref="downstream-policy-db",
        collection_name="policy-embeddings",
    )
```

`index_langchain_documents_with_remote_lineage()` validates the complete
batch before writing, calls `PGVector.add_documents()` with explicit IDs,
and verifies the returned IDs. It publishes one observation per chunk, **then**
the index observation. On partial/incorrect physical acknowledgement, it
publishes no observations. The binding call sends only the server-side
connection reference and collection name. It does not authorize invalidation.

## Failure and retry contract

PGVector, SDE observations, and SDE binding registration do **not** share a
transaction. Each HTTP POST can commit independently. A request that times out
may have committed even when the caller has no response. Do not automatically
retry ambiguous physical writes or advance a sequence without reconciliation.

1. Confirm actual PGVector chunks and revision metadata, using the read-only
   preflight where possible.
2. Replay the **same** immutable observations using the same chunk IDs,
   source, snapshot, index revision, and sequence; SDE reports `created=false`
   for an identical replay. A conflicting payload returns HTTP 409.
3. Register the pinned causal binding only after all chunk and index
   publications have completed, using the next valid binding revision.
4. Investigate any 409 before retrying with different identities. The HTTP
   client raises `SDEIntegrityHTTPError` with the status code, and deliberately
   does not embed potentially sensitive response bodies.

The onboarding helper never creates an invalidation decision, execution
intent, execution attempt, or verification. A successful HTTP response does
not prove that the physical collection is safe for deletion: run the separate
PGVector preflight and preserve SDE's execution authorization controls.

## Integration limitations

This is a synchronous HTTP-backed convenience layer, not a distributed
transaction or a generalized SDK. Cross-source and shared-collection
materializations are outside the current causal-chunk executor contract.
The six-test strict LangChain acceptance gate exercises real PGVector writes;
unit transport tests exercise the remote publication protocol. The remote
helper has not yet been validated as an end-to-end external deployment against
an independently hosted SDE instance.
