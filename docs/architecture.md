# Semantic Drift Engine Architecture

## 1. Purpose

Semantic Drift Engine is a Temporal Knowledge Integrity control plane.

Its job is not merely to detect that text changed. Its job is to preserve enough temporal and causal evidence to answer:

1. What knowledge changed?
2. Which exact downstream knowledge depended on it?
3. Why is a downstream asset considered impacted?
4. What invalidation decision was made?
5. Is that decision eligible for execution now?
6. Was execution authorized and attempted?
7. What physical side effect occurred?

The system is intended to sit between changing knowledge sources and downstream AI infrastructure such as RAG indexes, retrieval stores, derived documents, and agent knowledge.

## 2. Core Control Flow

```text
Upstream knowledge
        |
        v
Knowledge revision
        |
        v
Change evidence
        |
        v
Exact lineage observation
        |
        v
Dependency graph
        |
        v
Impact traversal
        |
        v
Invalidation decision
        |
        v
Current eligibility
        |
        v
Execution authorization
        |
        v
Attempt / claim fencing
        |
        v
Target resolution
        |
        v
Physical invalidation
        |
        v
Immutable execution result
```

Each stage represents a different fact. They should not be collapsed into a single synthetic lifecycle status.

## 3. Temporal Knowledge

SDE treats knowledge as versioned state.

A current document alone is insufficient for temporal integrity. The system needs to preserve evidence about:

- previous revisions,
- current revisions,
- when observations occurred,
- semantic changes between revisions,
- downstream consumption,
- decisions made from those facts,
- execution outcomes.

This enables historical questions that cannot be answered reliably from current state alone.

## 4. Exact Lineage

Exact lineage means that a downstream revision is connected to the exact upstream revision that was consumed.

This requires instrumentation before provenance is lost.

A typical relationship is:

```text
upstream asset A
revision A17
      |
      | consumed by
      v
downstream asset B
revision B4
```

SDE records this as lineage evidence rather than inferring it later.

### What exact lineage is not

Exact lineage is not:

- semantic similarity,
- embedding proximity,
- shared entities,
- URL similarity,
- graph centrality,
- temporal coincidence.

Those signals may provide useful contextual evidence, but they cannot prove exact consumption.

If a RAG or AI pipeline discards source revision identity before SDE observes the consumption, exact lineage cannot be reconstructed honestly afterward.

## 5. Lineage Observations

Lineage observations are the ingestion boundary for dependency evidence.

Observation kinds include:

- `materialization`
- `consumption`

A dependency reference identifies an upstream asset and revision and can carry a dependency type such as `derived_from`.

The organization associated with the authenticated API key defines the tenant boundary for public lineage operations.

## 6. Dependency Graph vs Entity Topology

SDE contains entity relationship and temporal topology capabilities.

These must not be confused with the Knowledge Dependency Graph.

### Entity topology

Entity topology describes relationships and structural context between entities.

It is useful for:

- topology shifts,
- graph metrics,
- contextual risk evidence,
- temporal structural analysis.

### Knowledge dependency graph

The dependency graph represents recorded causal consumption or derivation between exact knowledge revisions.

It is used for:

- impact analysis,
- causal paths,
- invalidation decisions,
- downstream execution.

Entity topology is evidence.

Exact lineage is causality.

The two must remain separate.

## 7. Impact Analysis

Impact analysis starts from an exact lineage revision identity.

The impact service traverses recorded downstream dependencies and determines which revisions are affected.

The result can include causal paths so the caller can inspect why a downstream revision is reachable from the changed root.

Two states must remain distinct:

```text
Exact lineage unavailable
```

and:

```text
Exact lineage available, zero downstream impact
```

Absence of provenance must never be presented as proof of zero impact.

## 8. Invalidation Decisions

An invalidation decision records a control-plane decision concerning an impacted target.

A decision is historical evidence.

It is not equivalent to current eligibility.

Policy or system state can change after a decision was created, so the system projects current eligibility independently.

This avoids a misleading synthetic status such as:

```text
executed = currently eligible
```

which is not necessarily true.

## 9. Execution Model

The execution foundation separates several concerns.

### Decision

What the control plane decided.

### Eligibility

Whether the decision is executable under current state and policy.

### Execution intent

An authorized intention to execute.

### Execution attempt

A claimed attempt to perform the side effect.

### Binding revision

The exact revision of execution-target configuration associated with an attempt.

### Execution result

Immutable evidence describing the outcome of the attempt.

This structure is designed to make retries and concurrent workers deterministic.

## 10. Delivery Semantics

The execution architecture targets:

```text
at-least-once processing
+
idempotent downstream side effects
```

It does not claim exactly-once distributed execution.

Attempt fencing, target binding revisions, and immutable result persistence reduce ambiguity around retries and concurrent execution, but they do not change the fundamental distributed-systems delivery model.

Webhook alert delivery follows the same at-least-once model. Receivers must treat the stable delivery key as an idempotency key because a request can be accepted externally before the worker durably records delivery success.

## 11. Execution Targets

Execution-target bindings connect logical invalidation targets to physical downstream systems.

Connection secrets are not intended to be persisted directly in lineage bindings. Bindings can reference externally supplied connection configuration.

The repository contains deterministic PGVector invalidation foundations and target-binding revision fencing.

The repository provides a lineage invalidation executor for previously authorized work, but the current Compose topology does not declare it as a long-running service.

## 12. Temporal-Lineage Correlation

Temporal observations and exact lineage are different evidence systems.

A temporal page snapshot does not automatically become an exact lineage revision merely because identifiers, URLs, entities, or content appear similar.

Temporal-lineage correlation must be explicit.

Therefore:

```text
temporal change
    +
no exact correlation
    =
lineage unavailable
```

not:

```text
zero impact
```

## 13. Semantic and Vector Intelligence

Semantic and vector analysis provide evidence about meaning and change.

The vector worker can coordinate stages including:

- embedding generation,
- vector analysis,
- risk scoring,
- pattern detection,
- anomaly detection.

Embedding generation can be disabled independently because it depends on an external provider.

Downstream deterministic stages can operate on evidence already persisted in PostgreSQL.

Supported embedding configuration includes OpenAI-compatible and Gemini providers.

## 14. Runtime Components

The current Compose topology deploys:

```text
Caddy (production override)
        |
        v
FastAPI API
   |         \
   v          v
PostgreSQL   Redis
   ^
   |
   +-- vector worker
   |
   +-- alert delivery worker
```

The repository also contains an email worker implementation, but the current Compose topology does not deploy it.

The current runtime also does not deploy:

- continuous crawler scheduling,
- autonomous lineage invalidation execution.

Those are runtime-closure tasks rather than capabilities that should be implied by documentation.

## 15. Persistence and Migrations

Database schema evolution is implemented through ordered SQL migrations under:

```text
scripts/migrations/
```

The migration runner is:

```text
scripts/migrate.py
```

Migration `000_base_schema.sql` provides the baseline mechanism, with subsequent forward migrations representing schema evolution.

The migration history includes exact lineage, reverse dependency lookup, invalidation decisions, policy activation, execution intents, execution attempts, immutable execution results, target bindings, binding revision fencing, and temporal-lineage correlations.

## 16. Health Semantics

`GET /health` is a liveness probe.

It deliberately avoids external dependency checks. A temporary PostgreSQL or Redis outage should not cause an otherwise healthy API process to be restarted solely because liveness failed.

`GET /ready` is a readiness probe.

It represents whether the service is ready to receive traffic that depends on its required infrastructure.

The production reverse proxy should therefore use readiness, while container process health may use liveness.

## 17. Security and Tenant Boundaries

Public lineage operations are organization-scoped through API-key authentication and explicit permissions.

Tenant identity must come from authenticated authority, not from caller-supplied organization identifiers.

The repository also contains user authentication, session management, CSRF handling, API-key lifecycle management, and security headers.

A dedicated production security review remains required before final production freeze.

## 18. Architectural Invariants

The following rules should be treated as invariants:

1. Do not infer exact lineage from semantic similarity.
2. Do not treat entity topology as the dependency graph.
3. Do not interpret missing lineage as zero impact.
4. Do not collapse historical execution and current eligibility.
5. Do not claim exactly-once execution.
6. Do not persist downstream connection secrets as lineage identity.
7. Do not expose internal worker fencing primitives as the public integration contract.
8. Preserve tenant isolation at every lineage and execution boundary.
9. Preserve revision identity before lossy transformation if exact lineage is required.
10. Prefer explicit evidence over reconstructed certainty.

These invariants define the trust model of Semantic Drift Engine.