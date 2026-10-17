# Semantic Drift Engine Lineage Integration

## 1. Purpose

This guide describes the supported end-to-end integration path for an external RAG, retrieval, or agent system using Semantic Drift Engine.

The integration preserves three separate trust boundaries:

1. application integration through organization-scoped API keys,
2. explicit operator execution authority,
3. deployment-managed downstream credentials and executor processes.

An application API key does not grant execution authority, and the executor does not create execution authority.

## 2. Integration Flow

```text
Publish logical target binding
        |
        v
Activate policy authority
        |
        v
Record exact lineage
        |
        v
Upstream revision changes
        |
        v
Temporal change evidence completes
        |
        v
Analyze temporal invalidation
        |
        v
Inspect persisted invalidation decision
        |
        v
Inspect current eligibility
        |
        v
Explicitly authorize execution
        |
        v
Executor processes authorized work
        |
        v
Inspect immutable terminal result
```

## 3. Application Integration

Create an API key for the organization that owns the lineage data and grant only the permissions required by the integration.

The API key determines tenant context. Application payloads must not select another organization.

If downstream invalidation is required, publish its logical execution target through:

```text
POST /api/lineage/execution-target-bindings
```

Target bindings are immutable revisions. Exact replay is idempotent, conflicting revisions fail closed, and revisions must remain contiguous.

Do not put physical database URLs, passwords, tokens, API keys, or other deployment secrets in target-binding metadata.

Record exact provenance while the consuming application still knows it:

```text
POST /api/lineage/observations
```

Use stable external asset and revision identifiers. Semantic similarity, embeddings, and entity relationships are not substitutes for exact provenance.

## 4. Supported LangChain Lineage Integration

SDE provides a supported LangChain lineage surface through:

```python
from src.integrations.langchain import (
    acquire_langchain_chunk_provenance,
    capture_langchain_consumer_binding,
    capture_langchain_index_materialization,
    prepare_langchain_documents_for_indexing,
)
```

The integration uses a structural document contract rather than requiring the SDE core to depend on LangChain. A compatible document provides:

- `id`: stable logical chunk identity when the upstream system provides one,
- `page_content`: exact chunk content,
- `metadata`: lineage metadata associated with the chunk.

The supported metadata keys are:

- `source_external_id`,
- `snapshot_external_id`,
- `chunk_revision_external_id`.

Preserve upstream logical chunk identity whenever it exists. When a document has no upstream identity, provenance acquisition may recover a previous logical identity only from exact unchanged content. Changed content without upstream identity is treated as a new logical chunk; the integration does not use semantic similarity to guess identity continuity.

Before writing documents to a vector store, use `prepare_langchain_documents_for_indexing` when SDE is responsible for acquiring chunk provenance. The preparation step acquires provenance and attaches the resulting exact chunk identity and revision to the same document objects that will be indexed. Existing stale `chunk_revision_external_id` metadata is replaced by the acquired revision. If document identity or metadata cannot be updated, preparation fails before the caller proceeds with the vector write.

`capture_langchain_index_materialization` produces canonical SDE materialization observations. The index observation depends on the exact chunk revisions through `indexed_from` dependencies.

`capture_langchain_consumer_binding` produces a canonical SDE consumption observation. Retrieved chunk revisions are recorded through exact `consumed` dependencies. Retrieval may contain different historical revisions from the same source; consumer provenance must preserve the revisions that were actually retrieved rather than rewriting them to the current snapshot.

These helpers capture lineage only. They do not activate policy authority, authorize execution, install downstream credentials, or execute invalidation.

For request-time LangChain checks, `query_langchain_context_integrity` requires the exact revision of the index or other serving materialization in addition to the retrieved documents. The serving revision is assessed first because invalidation decisions apply to downstream materializations, while the retrieved chunk revisions preserve the exact knowledge that entered the context. A chunk may therefore be `unknown` while its serving index is `affected`; callers must keep those facts distinct when applying their own fail-open or fail-closed policy.

LangChain PGVector invalidation remains behind the SDE execution boundary. Applications publish logical target bindings and exact lineage; deployment-managed credentials, explicit execution authority, claiming, fencing, and physical invalidation remain the responsibility of the SDE deployment and executor.

### Reproducible External LangChain Adoption Proof

The repository includes a runnable external-adopter proof in `experiments/external_langchain_rag.py`.

The proof deliberately keeps the adopter runtime separate from the SDE API runtime. LangChain dependencies are installed into an adopter-owned virtual environment rather than into the SDE API image. The adopter also uses a separate PGVector database provisioned by `experiments/docker-compose.external-langchain-rag.yml`.

From the repository root, create the isolated adopter environment:

```text
python -m venv .venv-external-rag
.venv-external-rag\Scripts\python -m pip install -r experiments\requirements.external_langchain_rag.txt
```

Start the isolated PGVector database:

```text
docker compose -f experiments\docker-compose.external-langchain-rag.yml up -d
```

The compose environment initializes the PostgreSQL `vector` extension automatically through `experiments/external_langchain_rag_init.sql`. No manual database setup is required for the proof.

Start the SDE API separately and verify that it is healthy:

```text
docker compose up -d db redis api
```

Create an organization-scoped API key through the supported authenticated API-key management flow. The proof requires `read=true` and `write=true`. API-key write permission permits application control-plane writes; it does not grant execution authority.

Set the raw key only in the adopter process environment:

```text
set SDE_API_KEY=<organization-api-key>
```

Do not commit the raw key or place it in target-binding metadata.

Run the external adopter workload as a module from the repository root:

```text
.venv-external-rag\Scripts\python -m experiments.external_langchain_rag
```

The proof performs the following supported application workflow:

1. writes deterministic LangChain documents into the isolated PGVector database,
2. captures and publishes the exact index materialization before that index serves retrieval,
3. retrieves a document and verifies that its exact lineage metadata survives the vector-store round trip,
4. queries `POST /api/lineage/context-integrity/query` before consumer use with both the exact serving index revision and the retrieved chunk revisions,
5. applies the proof application's policy: unresolved impact stops the request, while `unknown` remains visible in the returned evidence and is not rewritten as a safe state,
6. records consumer provenance only after the runtime integrity check has completed,
7. queries downstream impact through `POST /api/lineage/impact`,
8. verifies that the changed chunk reaches the exact index and consumer revisions while an unrelated chunk is excluded.

Observation publication is replay-safe. A repeated proof run may return existing observations rather than creating new rows; that is expected idempotent behavior and is not a failed proof.

This proof intentionally stops at the application/control-plane boundary. It does not activate policy authority, authorize invalidation, install downstream production credentials, or execute destructive downstream invalidation. Those remain separate operator and deployment responsibilities described below.

To remove the isolated proof database and its local data:

```text
docker compose -f experiments\docker-compose.external-langchain-rag.yml down -v
```

## 5. Deployment Installation

Physical downstream connection values belong to the deployment secret plane.

For database-backed execution, configure:

```text
SDE_DOWNSTREAM_DATABASE_URLS
```

to map logical connection references from target bindings to physical runtime database URLs.

The application integration API does not install these credentials.

## 6. Activate Policy Authority

Policy activation is an explicit operator action:

```text
python -m scripts.lineage_authority activate-policy --organization-id <organization-uuid> --policy-external-id <policy-id> --revision <revision>
```

Activation is tenant-scoped and revisioned.

An exact activation replay is idempotent. A stale activation must not replace newer authority.

## 7. Analyze Temporal Invalidation

When SDE has completed semantic and vector evidence for a temporal drift event, request production invalidation analysis through:

```text
POST /api/lineage/temporal-invalidation-analysis
```

The request contains the temporal drift `event_id`. Tenant context is derived from the authenticated API key; callers do not supply an organization identifier.

This is a control-plane write operation because it persists immutable invalidation decisions. It requires API-key `write` permission.

The operation resolves exact lineage roots correlated with the event's old snapshot, traverses their recorded downstream dependencies, evaluates the canonical SDE invalidation policy, and persists the resulting decisions.

It does not activate policy authority, authorize execution, install deployment credentials, or execute downstream invalidation.

Required semantic and vector change evidence must already be complete. If that evidence is not ready, analysis fails closed rather than treating the event as a no-op.

A successful response with no root analyses is a deterministic no-op: no exact lineage roots were correlated with the stale snapshot. This is distinct from change evidence not being ready.

Replaying the same analysis is safe. Immutable decision identity is replay-safe and does not create duplicate decisions.

`POST /api/lineage/impact` remains available when an application needs direct impact analysis for an exact lineage revision without starting from a temporal drift event.

## 8. Discover and Inspect Decisions

Discover invalidation decisions through:

```text
POST /api/lineage/invalidation-decisions/query
```

Decision discovery does not execute downstream work.

Inspect a specific decision through:

```text
GET /api/lineage/invalidation-decisions/{decision_id}
```

The control-plane representation keeps the persisted decision, current eligibility, execution intent, attempts, and terminal result distinct.

## 9. Authorize Execution

Execution authorization is deliberately outside the application API-key surface.

An operator authorizes an eligible persisted decision with:

```text
python -m scripts.lineage_authority authorize-execution --organization-id <organization-uuid> --decision-id <decision-uuid>
```

Authorization is tenant-scoped and idempotent for the same decision.

Eligibility is evaluated against current authority before an execution intent is created.

API-key `write` permission is not execution authority.

## 10. Run the Executor

Run the executor as a supervised process when autonomous processing of authorized work is required:

```text
python -m scripts.run_lineage_invalidation_executor
```

The executor consumes existing execution authority. It does not authorize decisions itself.

Runtime processing is at-least-once and protected by durable claiming and fencing. Do not assume exactly-once process execution.

## 11. Inspect Execution and Verify Post-Repair Evidence

After authorization and processing, retrieve:

```text
GET /api/lineage/invalidation-decisions/{decision_id}
```

Inspect the control-plane execution state and immutable result instead of inferring execution from the historical decision.

Historical execution and current eligibility are separate facts. A terminal result with `outcome: succeeded` means that the executor completed the downstream invalidation side effect. It is not, by itself, proof that the downstream materialization now reflects the replacement truth.

A successful result initially projects:

```text
awaiting_evidence
```

when no explicit post-repair evidence has been bound.

After the downstream system rematerializes the affected target, publish a new lineage `materialization` observation through:

```text
POST /api/lineage/observations
```

The observation must describe the exact materialized target revision and its exact upstream dependencies. Capture this evidence while the downstream system still knows those identities.

Then explicitly bind that observation to the successful execution result:

```text
POST /api/lineage/invalidation-execution-verifications
```

with:

```json
{
  "execution_result_id": "<execution-result-uuid>",
  "observation_id": "<materialization-observation-uuid>"
}
```

This publication is an explicit causal assertion: the supplied materialization observation is the post-repair evidence for that successful execution result. SDE does not infer this relationship from timestamps, observation sequence, or revision advancement.

The bound observation must be a materialization for the exact persisted decision target. An exact replay of the same binding is idempotent. A successful result cannot be rebound to a different observation.

The read projection then classifies the evidence as:

- `awaiting_evidence`: successful execution exists, but no explicit evidence has been bound,
- `stale_dependency_present`: the bound materialization still contains the exact stale direct predecessor from the persisted causal path,
- `verified`: the bound materialization no longer contains that exact stale direct predecessor.

`verified` is intentionally a narrow integrity predicate. It does not assert that the entire downstream system is globally safe, repaired, resolved, or that integrity has been restored beyond this specific persisted dependency condition.

For an event-level view, retrieve:

```text
GET /api/lineage/temporal-invalidation-resolution/{event_id}
```

This endpoint uses durable event-to-decision provenance to return the associated control-plane records. It does not rerun historical graph traversal or policy evaluation.

The supported post-repair workflow is therefore:

```text
temporal drift
    -> persisted invalidation decision
    -> explicit execution authorization
    -> successful executor terminal result
    -> awaiting_evidence
    -> new materialization observation
    -> explicit result-to-observation evidence publication
    -> stale_dependency_present | verified
```

API-key `write` permission permits evidence publication; it does not grant execution authority.

## 12. Responsibility Boundary

The external application owns:

- stable asset and revision identities,
- exact provenance capture,
- API-key lifecycle,
- lineage observation publication,
- temporal invalidation analysis when reacting to completed drift events,
- post-repair materialization evidence publication,
- impact, decision, resolution, and verification-state consumption.

The SDE operator owns:

- policy activation,
- explicit execution authorization,
- physical downstream connection installation,
- executor deployment and supervision,
- deployment secret management.

SDE owns:

- tenant-scoped lineage persistence,
- dependency impact traversal,
- invalidation decisions,
- durable temporal-event-to-decision provenance,
- current eligibility evaluation,
- execution-intent persistence,
- claim and fencing semantics,
- deterministic execution coordination,
- immutable execution results,
- durable execution-to-verification evidence binding,
- deterministic post-repair verification projection.

## 13. Integration Invariants

Do not:

- substitute semantic similarity for exact provenance,
- place physical credentials in target-binding metadata,
- allow workers to auto-authorize execution,
- treat API-key `write` permission as execution authority,
- bypass tenant scope with client-supplied organization identifiers,
- treat incomplete temporal change evidence as a no-op,
- assume exactly-once execution,
- infer current eligibility from historical execution.