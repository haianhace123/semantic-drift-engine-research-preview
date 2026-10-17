# LangChain PGVector causal execution onboarding

This guide covers the supported `langchain_pgvector_causal_chunk` integration.
It assumes the SDE authority database is provisioned and the downstream
PostgreSQL database uses the LangChain PGVector schema. The ingestion service
that writes the index is a trusted component. SDE does not infer collection
ownership from an execution binding.

## 1. Provision a dedicated collection

One physical collection belongs to one SDE organization, source and logical
index. Do not reuse it across different SDE index identities.

```python
from langchain_postgres import PGVector
from src.integrations.langchain import sde_pgvector_collection_metadata

store = PGVector(
    embeddings=embedding_model,
    connection=pgvector_psycopg_dsn,
    collection_name=collection_name,
    collection_metadata=sde_pgvector_collection_metadata(
        organization_id=organization_id,
        source_external_id="policy-source",
        index_external_id="policy-index",
    ),
    use_jsonb=True,
)
```

Provision identity at collection creation. For an existing collection, first
independently verify its real ownership and physical contents. Never backfill
these fields from the current execution binding. Restrict downstream database
privileges that can edit `langchain_pg_collection.cmetadata`.

## Recommended in-process integration (trusted index writer)

The two public convenience helpers avoid manually translating LangChain documents
into SDE observations or constructing binding objects. They do not require
LangChain at import time; the caller supplies an existing PGVector store.

```python
from src.integrations.langchain import (
    index_langchain_documents_with_lineage,
    register_langchain_pgvector_causal_target,
)

# Store was provisioned with sde_pgvector_collection_metadata() above.
# Documents carry stable IDs plus source_external_id and snapshot_external_id.
indexed = index_langchain_documents_with_lineage(
    vector_store=store,
    authority_db=authority_db,
    organization_id=organization_id,
    source_external_id="policy-source",
    documents=documents,
    index_external_id="policy-index",
    index_revision_external_id="policy-index-r17",
    sequence=17,
)
register_langchain_pgvector_causal_target(
    authority_db=authority_db,
    organization_id=organization_id,
    source_external_id="policy-source",
    index_external_id="policy-index",
    connection_ref="downstream-policy-db",
    collection_name=collection_name,
    binding_revision=1,
)
authority_db.commit()
```

The indexing helper validates the whole batch, calls `add_documents()` with
explicit chunk IDs, checks the returned IDs, and only then stages chunk and
index materializations. The returned `indexed.materialization` records exact
revision evidence. The binding helper stages only an immutable target binding.
**Neither helper authorizes deletion or commits the authority transaction.**
The caller must commit/rollback the authority DB, manage retries, and confirm
collection ownership independently (use the read-only preflight). The PGVector
write and authority commit are **not atomic**: on a partial/ambiguous physical
write or authority publication failure, reconcile actual PGVector contents
before retrying. Do not claim the index is ready purely from the helper return.

This is a trusted **in-process** Python integration using the SDE authority DB
session, not a remote customer-facing API or an automatically authorized executor.
The explicit low-level sequence below remains available for custom ingestion.

## 2. Write chunk provenance and record materialization

Give each logical chunk a stable, nonempty `Document.id`. In the tested PGVector
schema, embedding IDs are globally unique, not unique only within a collection.
Ensure IDs do not collide across collections. Each document must contain
`source_external_id` and `snapshot_external_id` metadata.

```python
from langchain_core.documents import Document
from src.integrations.langchain import (
    prepare_langchain_documents_for_indexing,
    capture_langchain_index_materialization,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)

prepared = prepare_langchain_documents_for_indexing(
    documents=[
        Document(
            id="policy-source:refund-rule",
            page_content="Refunds require manager approval.",
            metadata={
                "source_external_id": "policy-source",
                "snapshot_external_id": "policy-snapshot-17",
            },
        ),
    ],
)
store.add_documents(list(prepared.documents))

captured = capture_langchain_index_materialization(
    documents=prepared.documents,
    index_external_id="policy-index",
    index_revision_external_id="policy-index-r17",
    sequence=17,
)
for observation in captured.observations:
    persist_lineage_observation(
        authority_db,
        organization_id=organization_id,
        observation=observation,
    )
authority_db.commit()
```

The writer must verify successful physical writes before publishing the
corresponding materialization evidence. Handle failure between the PGVector
write and authority commit explicitly, with reconciliation or retry. An index
`consumption` observation is not a substitute for recorded index
`materialization` dependencies.

## 3. Register an execution target

Use the real downstream connection reference configured in SDE; never place
credentials or physical document IDs in an execution binding.

```python
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)

binding = LineageExecutionTargetBinding(
    identity=LineageExecutionTargetIdentity("policy-source", "policy-index"),
    binding_revision=1,
    provider="pgvector",
    target_kind="langchain_pgvector_causal_chunk",
    configuration={
        "connection_ref": "downstream-policy-db",
        "collection_name": collection_name,
    },
)
persist_lineage_execution_target_binding(
    authority_db,
    organization_id=organization_id,
    binding=binding,
)
authority_db.commit()
```

Binding revisions are immutable and sequential; use the next revision to
change connection or collection. Historical attempts remain pinned to the
revision they claimed. Both legacy PGVector target kinds are not executable.

## 4. Run the read-only physical preflight

Before enabling deletion, check the collection identity independently of the
execution binding. SDE reads the connection from the configured
`DOWNSTREAM_DATABASE_URLS` registry. Do not paste database credentials into
CLI arguments, Git commits, or screenshots.

```powershell
# Run from the repository root with the downstream registry configured in SDE.
docker compose run --rm -v "${PWD}:/app" api python -m scripts.lineage_pgvector_preflight `
    --organization-id "<organization-uuid>" `
    --source "policy-source" `
    --index "policy-index" `
    --connection-ref "downstream-policy-db" `
    --collection "<physical-collection-name>" `
    --chunk-id "policy-source:refund-rule" `
    --chunk-revision "<revision-from-preparation>"
```

The command prints one JSON record. Exit code `0` means the requested
**physical** checks passed; `2` means a preflight failure. It only uses
parameterized SQL `SELECT` queries and never deletes or retags data. If chunk
arguments are omitted, only collection identity is checked. Both chunk
arguments must be provided together.

Diagnostic codes are `collection_not_found`, `collection_identity_missing`,
`collection_identity_mismatch`, `chunk_not_found`,
`chunk_provenance_mismatch`, `invalid_arguments`, and
`downstream_unavailable`. Check provisioning, connection registry, actual
collection ownership, LangChain document ID, or physical revision according
to the code. Never fix a mismatch by copying a binding's identity tags onto
an unverified collection.

**A preflight READY status is not authorization.** It does not query the SDE
causal graph, grant an execution intent, or verify index repair. Physical rows
can change after preflight; the executor independently locks and rechecks
identity during deletion.

## 4. Authorize, execute, and verify separately

Only after the current policy and an invalidation decision have been persisted
should an authorized operator create the execution intent. The decision's
causal path must include exactly one evidenced chunk revision, and the final
index dependency must appear on an index materialization observation. The
executor verifies tenant, source, index, chunk ID and chunk revision before
any delete, and rejects mismatched or untagged collections.

Execution `succeeded` means the authorized single-chunk action completed; it
does not mean a multi-chunk fan-out ran, and it is not an integrity-verification
result. Publish separate post-repair evidence and query integrity status before
considering the index recovered.

## 6. Acceptance and operational checks

The dedicated acceptance gate runs six real LangChain/PGVector tests and fails
if any are skipped, missing, or failed. Its optional packages are declared in
`requirements.integration-langchain.txt` and installed in an isolated
temporary virtual environment by CI. The production image does not need them.

Use the `Real LangChain PGVector execution acceptance (no skips)` job step in
`.github/workflows/ci.yml` as the release signal for this integration. Also
validate the downstream role permissions, existing collection identity, and
reconciliation of uncommitted lineage evidence in the deployment environment.

See `docs/causal_pgvector_collection_identity.md` for the physical trust
boundary and migration restrictions.
