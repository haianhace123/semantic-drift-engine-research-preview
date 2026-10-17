# Temporal Knowledge Integrity Benchmark v0

## Purpose

This benchmark evaluates whether the mechanisms behind Semantic Drift Engine can identify consequential knowledge change and determine its downstream operational impact.

The benchmark is intentionally separated into distinct capabilities:

1. semantic change magnitude
2. structural topology change
3. dependency impact and invalidation

These capabilities must not be interpreted as interchangeable signals.

The benchmark is diagnostic. It is not a claim of production accuracy or product-market fit.

## Benchmark integrity rules

The following rules apply to v0:

- calibration and evaluation data are separated where thresholds are required
- evaluation thresholds are frozen after calibration
- higher detector scores mean more likely consequential change
- benchmark labels are not used to construct production scorer inputs
- failed hypotheses are preserved rather than tuned away
- corpus definitions are frozen after their first measurement
- production scoring primitives are reused where available
- dependency-impact ground truth is evaluated independently from detector thresholds

## Stage 5B - Semantic magnitude

### Question

Can embedding-space movement distinguish consequential semantic changes from benign rewrites at a low false-positive rate?

### Production mechanism

The benchmark uses the production embedding path and production vector metrics.

Semantic magnitude is derived from cosine similarity and is therefore not an independent detector from embedding cosine distance.

### Corpus

40 cases arranged as 20 adversarial pairs:

- 10 calibration pairs
- 10 evaluation pairs

Mutation families:

- numeric critical change
- negation
- threshold change
- restriction deletion
- authority change

Each pair shares the same source text and contains:

- one benign rewrite
- one consequential critical mutation

### Frozen Gemini result

Provider:

- model: `gemini-embedding-2`
- dimensions: 1536

Evaluation:

| Metric | Result |
| --- | ---: |
| Calibration threshold | 0.031936360951497933 |
| Precision | 1.0000 |
| Recall | 0.5000 |
| F1 | 0.6667 |
| False-positive rate | 0.0000 |
| Critical-change recall | 0.5000 |
| Calibration paired discrimination | 0.9000 |
| Evaluation paired discrimination | 0.9000 |

Evaluation paired outcomes:

- correct: 9/10
- tied: 0/10
- incorrect: 1/10

### Interpretation

Embedding magnitude provides useful relative semantic signal.

However, at zero evaluation false positives, the frozen threshold detects only half of the critical changes.

Small but consequential numeric and threshold changes are a clear weakness.

Semantic magnitude should therefore be treated as a signal, not as the definition of consequentiality.

## Stage 5C - Topology shift

### Question

When semantic magnitude is insufficient, does structural graph movement reliably identify consequential structural change?

### Production mechanism

The benchmark uses the production graph metric calculation and topology comparison implementation.

Topology shift v1 measures a weighted magnitude of:

- degree-centrality movement
- downstream-reach-ratio movement
- normalized local-degree movement

### Corpus

24 cases arranged as 12 structural pairs:

- 6 calibration pairs
- 6 evaluation pairs

Mutation families include:

- dependency path
- authority route
- control bypass
- propagation change
- approval chain
- dependency rewire

### Frozen result

| Metric | Result |
| --- | ---: |
| Calibration threshold | 0.20000000000000004 |
| Precision | 1.0000 |
| Recall | 0.5000 |
| F1 | 0.6667 |
| False-positive rate | 0.0000 |
| Critical-change recall | 0.5000 |
| Calibration paired discrimination | 0.0000 |
| Evaluation paired discrimination | 0.5000 |

Evaluation paired outcomes:

- correct: 3/6
- tied: 1/6
- incorrect: 2/6

Critical mutations missed by the frozen threshold include:

- access-path mutation
- backup-path mutation
- agent-dependency mutation

### Interpretation

Topology shift magnitude is not a reliable proxy for structural consequentiality.

Some consequential rewires produce less topology movement than benign structural controls.

The benchmark therefore falsifies the stronger hypothesis that topology-shift magnitude alone is sufficient to prioritize consequential structural change.

The result does not falsify the broader Semantic Drift Engine thesis.

It indicates that structural movement and operational consequence are different concepts.

### Limitation

The v0 structural corpus contains some asymmetry.

Several successful consequential cases involve larger structural or population changes than their benign controls.

This may make those cases easier for a topology-magnitude detector.

The corpus was not modified after measurement.

## Stage 5D - Dependency impact

### Question

Once an upstream knowledge state is known to have changed, can explicit dependency propagation identify downstream state that may no longer be safe to trust?

### Dependency semantics

An edge:

`A -> B`

means:

`B` depends on state derived from `A`.

If `A` becomes invalid, `B` may therefore become invalid or operationally affected.

The dependency graph is distinct from the entity relationship graph used by topology shift.

### Impact model

The v0 primitive separates:

- directly affected state
- transitively affected state
- critical downstream surfaces
- materialized state that can be invalidated or rebuilt
- deterministic shortest provenance paths from changed roots

The primitive is cycle-safe and supports multiple changed roots.

### Corpus

Six adversarial dependency scenarios:

- multi-hop propagation
- fan-out
- irrelevant branch exclusion
- shared downstream dependency
- multiple changed roots
- topology-unchanged truth change

### Baselines

Three deterministic methods are compared:

1. `direct_only`
2. `materialized_direct_only`
3. `dependency_transitive`

No threshold or calibration is used.

### First frozen measurement

#### direct_only

| Dimension | Precision | Recall | F1 | Exact case rate |
| --- | ---: | ---: | ---: | ---: |
| Affected assets | 1.0000 | 0.3200 | 0.4848 | 0.0000 |
| Invalidation candidates | 1.0000 | 0.5333 | 0.6957 | 0.0000 |
| Critical assets | 1.0000 | 0.0000 | 0.0000 | 0.0000 |

#### materialized_direct_only

| Dimension | Precision | Recall | F1 | Exact case rate |
| --- | ---: | ---: | ---: | ---: |
| Affected assets | 1.0000 | 0.3200 | 0.4848 | 0.0000 |
| Invalidation candidates | 1.0000 | 0.5333 | 0.6957 | 0.0000 |
| Critical assets | 1.0000 | 0.0000 | 0.0000 | 0.0000 |

#### dependency_transitive

| Dimension | Precision | Recall | F1 | Exact case rate |
| --- | ---: | ---: | ---: | ---: |
| Affected assets | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Invalidation candidates | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Critical assets | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### Interpretation

The perfect dependency-transitive result must not be interpreted as production accuracy.

The corpus tests propagation semantics that the primitive is explicitly designed to implement.

The meaningful diagnostic result is the failure of local-only baselines to recover downstream impact:

- direct affected-asset recall: 32.0%
- direct invalidation recall: 53.33%
- direct critical-asset recall: 0.0%

This demonstrates that immediate change locality is insufficient to represent downstream operational consequence in these fixtures.

The `topology_unchanged_truth_change` scenario also captures an important architectural distinction:

knowledge values can change while dependency topology remains unchanged.

In that situation, topology-shift magnitude may be zero even though previously derived state can become stale.

## Stage 6A - Deterministic RAG invalidation

### Question

Given a known upstream knowledge change, can targeted dependency-aware invalidation restore derived RAG state with less reprocessing than a full rebuild?

How does the result degrade when observed causal lineage is incomplete?

### Experiment model

The deterministic experiment separates three operations:

1. invalidation strategy selects asset IDs
2. rebuild execution recomputes selected assets in dependency order
3. answer evaluation observes resulting materialized state independently of strategy

The evaluator does not receive the selected strategy.

The corpus defines expected affected assets independently from the observed dependency traversal so that missing lineage can reduce measured impact recall.

### Corpus

Six production-like dependency shapes are included:

- complete linear lineage
- missing terminal lineage edge
- missing middle lineage edge
- fan-out
- unrelated branch
- shared dependency

All six evaluation questions are critical.

Four strategies are compared:

1. `no_invalidation`
2. `direct_only`
3. `dependency_transitive`
4. `full_rebuild`

### First frozen measurement

| Strategy | Stale-answer rate | Critical stale-answer rate | Affected-asset recall | Reprocessing units |
| --- | ---: | ---: | ---: | ---: |
| `no_invalidation` | 1.0000 | 1.0000 | 0.0000 | 0 |
| `direct_only` | 1.0000 | 1.0000 | 0.3000 | 6 |
| `dependency_transitive` | 0.3333 | 0.3333 | 0.8500 | 17 |
| `full_rebuild` | 0.0000 | 0.0000 | 1.0000 | 22 |

In this corpus, dependency-transitive invalidation performs 17 of the 22 reprocessing units required by full rebuild.

This is a reduction of:

`5 / 22 = 22.73%`

relative to full rebuild.

### Dependency-transitive case outcomes

| Case | Affected-asset recall | Stale answers | Critical stale answers | Reprocessing units |
| --- | ---: | ---: | ---: | ---: |
| complete lineage | 1.0000 | 0 | 0 | 3 |
| missing terminal edge | 0.6667 | 1 | 1 | 2 |
| missing middle edge | 0.3333 | 1 | 1 | 1 |
| fan-out | 1.0000 | 0 | 0 | 4 |
| unrelated branch | 1.0000 | 0 | 0 | 3 |
| shared dependency | 1.0000 | 0 | 0 | 4 |

### Interpretation

When observed causal lineage is complete in these fixtures, dependency-transitive invalidation restores answer correctness across linear, fan-out, unrelated-branch, and shared-dependency shapes.

The missing-lineage cases deliberately falsify the stronger assumption that correct traversal alone is sufficient.

A missing terminal edge leaves the critical answer stale with 66.67% affected-asset recall.

A missing middle edge leaves the critical answer stale with 33.33% affected-asset recall.

The experiment therefore shifts the primary unresolved technical risk from dependency traversal to lineage acquisition quality.

Targeted invalidation is only as reliable as the causal lineage available to the control plane.

The measured 22.73% reprocessing reduction is diagnostic only.

The corpus is too small and contains too high a proportion of affected assets to support an economic claim about production workloads.

### Measurement limitation

The Stage 6A affected-set aggregate intentionally measures completeness rather than over-invalidation precision.

Predicted IDs outside the independently labeled affected set are excluded before the affected-set metric is calculated.

Unnecessary work is represented by `reprocessing_units`.

Therefore the reported affected-set precision must not be interpreted as production invalidation precision.

### Stage 6A conclusion

The deterministic invalidation mechanism is sufficient to justify moving to a production-like RAG integration experiment.

The next question is no longer whether transitive dependency propagation works on a known graph.

It is:

> Can Semantic Drift Engine acquire sufficiently complete causal lineage from a production-like RAG stack, with low enough instrumentation burden, that targeted invalidation preserves correctness while materially reducing unnecessary recomputation?

## Stage 6B - RAG lineage acquisition boundaries

### Question

What information must Semantic Drift Engine observe from an external RAG materialization lifecycle to reconstruct high-confidence causal lineage?

Can that lineage be inferred after downstream representations have discarded source provenance?

### Experiment model

Stage 6B separates the external RAG lifecycle from the Semantic Drift Engine lineage contract.

The external stack does not construct `MaterializationEvent` objects.

An adapter observes external materialization boundaries and translates the available information into SDE lineage events.

The controlled lifecycle covers:

`source -> snapshot -> chunk -> index -> agent`

The experiment focuses on logical identity, revision identity, causal revision dependencies, and information loss across integration boundaries.

It does not measure production framework integration cost.

### Stable chunk identity

Ordinal chunk identity was first tested as a fallback.

A prefix insertion moved unchanged content to a different ordinal and therefore changed its logical asset identity.

Exact-content historical matching preserved identity across positional movement and distinguished duplicate content by occurrence, but it failed when the content of the same logical chunk changed.

Explicit stable external identity preserved the logical asset across content mutation while allowing its revision identity to change.

Duplicate stable identities are rejected.

The resulting high-confidence identity rule is:

> Logical chunk identity must come from a stable upstream identifier or equivalent structural anchor when continuity across content mutation matters.

Text-only matching may be useful as a lower-confidence fallback, but the current experiment does not establish it as causal truth.

### Pre-lossy materialization boundary

When the adapter observes an index materialization containing stable chunk IDs and the exact chunk values being written, it can derive deterministic chunk and index revisions and reconstruct:

- logical chunk-to-index dependencies
- exact chunk-revision-to-index-revision dependencies
- index-to-agent dependencies when the exact consumed index revision is supplied

The external stack does not need to know the SDE `MaterializationEvent` schema.

A second boundary deliberately exposes only vector IDs and embeddings after serialization.

At that boundary, exact chunk revision provenance is unavailable.

The adapter rejects the observation rather than manufacturing causal lineage.

The experiment therefore establishes the following boundary condition:

> High-confidence causal lineage must be captured at or before the point where materialized knowledge provenance is discarded by a lossy downstream representation.

A vector-store write boundary is not sufficient when it exposes only vector identifiers and embeddings.

### Exact consumer revision binding

The experiment also tests a mixed-version deployment.

Two revisions of the same logical index are observed while an agent remains bound to the older revision.

Using only:

`agent_id + index_id`

causes an adapter that assumes the latest observed index revision to record the wrong causal dependency.

The latest-observed assumption is therefore falsified.

The corrected contract requires the consuming surface to provide the exact index revision it consumes.

Historical upstream revisions remain valid causal dependencies.

If the exact consumed revision is absent, the adapter fails closed.

The resulting rule is:

> Logical dependency identity is not sufficient to establish revision-level causality in a mixed-version system.

### Instrumentation surface

The current controlled RAG model contains 11 metadata fields across chunk, index, and agent boundaries.

They are classified as:

| Ownership | Fields |
| --- | ---: |
| Customer required | 3 |
| Customer or adapter | 4 |
| SDE derived | 4 |

The customer-required fields in this model are:

- stable chunk asset identity
- consumed chunk revisions at index materialization
- exact consumed index revision at the consumer binding

The touchpoint model currently hypothesizes:

- one explicit chunk-identity boundary
- one pre-lossy materialization adapter hook
- one consumer-revision binding adapter hook
- one source/snapshot boundary requiring no additional touch

These counts describe the controlled integration surface only.

They must not be interpreted as evidence that production integration requires only one customer code change or that instrumentation cost is low.

### Stage 6B findings

The controlled experiment supports five high-confidence lineage requirements:

1. logical knowledge units need stable identity across meaningful revisions
2. exact upstream revisions must be observable at materialization time
3. consuming surfaces must identify the exact revision they consume
4. causal provenance must be captured before lossy serialization discards it
5. missing or unobserved causal provenance must fail closed rather than be replaced by a latest-observed assumption

These results narrow the lineage-acquisition problem substantially.

They do not yet establish dependency-discovery precision or recall on a real RAG framework, vector database, or agent runtime.

They also do not establish that the required instrumentation is operationally cheap.

### Stage 6B conclusion

Stage 6A showed that incomplete causal lineage can leave critical derived state stale.

Stage 6B identifies concrete information boundaries that can create that incompleteness.

The unresolved question is no longer whether arbitrary post-hoc observation can reconstruct exact lineage.

It cannot when required provenance has already been discarded.

The next production-like integration experiment should therefore test whether common RAG and agent stacks expose the required identity and revision information at practical hook points without invasive application changes.

## Stage 6C-5: LangChain and PGVector real-stack falsification

Stage 6C-5 moves lineage acquisition from the controlled external-stack model into an isolated integration with LangChain Python and PGVector.

The experiment uses:

- `langchain-core==1.6.3`
- `langchain-text-splitters==1.1.2`
- `langchain-postgres==0.0.18`
- a real PostgreSQL and pgvector persistence boundary
- deterministic embeddings so lineage behavior is tested independently of embedding-provider behavior

The LangChain dependencies remain isolated from the production SDE environment.

The experiment observed an incompatible transitive dependency boundary: the tested LangChain stack resolves `websockets==17.1`, while the production `google-genai==1.2.0` dependency requires `websockets>=13,<15`.

Stage 6C-5 therefore does not justify adding LangChain packages to the production SDE dependency set.

### Splitter provenance boundary

`RecursiveCharacterTextSplitter` preserves document metadata but does not preserve the parent `Document.id` when it creates chunk documents.

The optional `start_index` metadata is a position, not a stable logical chunk identity. Prefix insertion and content mutation can change chunk boundaries and offsets.

The experiment therefore classifies chunk provenance acquisition as follows:

| Provenance | Result |
| --- | --- |
| Stable source identity | Upstream/native identity required |
| Source revision or snapshot | Adapter-owned or derivable |
| Source and snapshot propagation | Preserved by LangChain metadata |
| Unchanged chunk identity | Adapter recoverable from prior exact materialization |
| Newly observed chunk identity | Adapter assignable with snapshot-scoped identity |
| Mutated chunk identity continuity | Upstream/customer stable identity required for exact continuity |
| Chunk revision identity | Adapter derivable from exact materialized content |

Exact logical continuity across a mutated chunk cannot in general be reconstructed post-hoc from standard splitter output when no stable upstream chunk identity exists.

Similarity, position, or neighboring-content heuristics could provide inferred correspondence, but they would not establish exact causal identity and are therefore not treated as exact lineage.

### PGVector persistence boundary

Real LangChain PGVector round trips preserve the causal provenance carried in document metadata.

The experiment distinguishes three identities:

`storage identity != logical asset identity != causal revision identity`

A PGVector row identifier is therefore not treated as a causal revision identifier.

Historical revisions can coexist when storage identities are revision-distinct while stable logical asset identity and causal revision identity are carried separately.

PGVector itself accepts documents with missing causal revision provenance.

The SDE lineage boundary rejects such documents rather than inferring a revision from current or latest state.

### Retrieval and consumer binding boundary

Retrieval preserves the exact causal revision metadata attached before vector persistence.

The consumer-binding adapter records dependencies from the documents actually returned by retrieval rather than resolving a latest observed revision.

The experiment demonstrates that:

- a historical revision can remain the exact consumed dependency after a newer revision exists
- one consumer binding can consume exact revisions originating from multiple source snapshots
- one logical asset can contribute multiple distinct consumed revisions without those revisions being collapsed
- missing exact consumed revision provenance fails closed
- mixed-source consumer bindings remain outside the current source-scoped projection contract

Consumer binding has an explicit snapshot context that is separate from the exact revisions consumed by the consumer.

The binding snapshot therefore does not replace or rewrite revision-level causal dependencies.

### End-to-end lineage reconstruction

The real retrieval path was exercised through:

`PGVector retrieval -> consumer binding -> MaterializationEvent -> MaterializationProjection -> SnapshotLineageObservation -> revision dependencies`

Exact mixed-snapshot consumed revisions survive this path without being replaced by the current snapshot or latest observed revision.

The existing materialization model can represent this because the projection records observed snapshots separately while revision dependencies retain exact upstream asset and revision identifiers.

### Stage 6C-5 falsification result

The strong hypothesis that exact causal lineage can always be reconstructed post-hoc from a production-like RAG stack is falsified.

In particular, exact logical chunk continuity across mutation is unavailable when stable upstream identity has already been discarded.

A narrower integration hypothesis remains supported.

For the tested LangChain and PGVector path, trustworthy causal lineage can be captured with two principal adapter boundaries:

1. a pre-vector-write boundary that attaches or verifies stable identity and exact materialization provenance
2. a retrieval-time boundary that records the exact revisions actually consumed

This is not zero-instrumentation lineage acquisition.

The minimum trustworthy contract still requires stable upstream identity wherever exact logical continuity must survive content mutation.

Stage 6C-5 therefore narrows the product architecture toward explicit causal provenance capture rather than post-hoc lineage inference.

## Combined findings

The v0 benchmark supports the following separation:

`semantic magnitude != consequentiality`

`topology magnitude != consequentiality`

`dependency impact != change detection`

Semantic magnitude answers:

> How much did meaning-space move?

Topology shift answers:

> How much did graph structure move?

Dependency impact answers:

> Given that upstream knowledge changed, what downstream state may no longer be safe to trust?

These are complementary capabilities.

They should not be collapsed into one undifferentiated drift score.

## Architectural implication

The strongest hypothesis emerging from v0 is not that Semantic Drift Engine needs a better scalar drift score.

It is that production AI systems contain derived knowledge state whose validity depends on upstream knowledge.

A consequential upstream change creates a potential invalidation event.

A temporal knowledge integrity system therefore needs to:

1. detect relevant upstream change
2. identify downstream dependencies
3. calculate the affected blast radius
4. determine targeted invalidation or recomputation candidates
5. execute or request repair
6. verify that integrity has been restored

This moves the architecture toward a change-control and invalidation layer for AI knowledge rather than a generic semantic-change dashboard.

## Current limitations

The benchmark remains v0.

Important limitations include:

- small synthetic corpora
- limited negative-case diversity
- coarse false-positive-rate resolution
- possible lexical cues in semantic cases
- structural corpus asymmetry
- no real customer or production dependency lineage
- framework-backed testing currently covers LangChain and PGVector only
- no measured dependency discovery precision or recall from customer production workloads
- no production customer RAG integration
- no measured production instrumentation cost
- exact mutated-chunk continuity still requires stable upstream identity
- no portability validation across additional RAG frameworks
- stale-answer recovery measured only in a deterministic controlled experiment
- reprocessing savings measured only in a small synthetic controlled corpus
- no customer evidence or willingness-to-pay evidence

Stage 5D assumes a correct dependency graph.

Stage 6A removes that assumption for controlled missing-lineage cases and demonstrates that incomplete causal lineage can leave critical derived state stale.

Stage 6C-5 shows that sufficiently exact revision-level lineage can be carried through a production-like LangChain and PGVector path, but not reconstructed universally after stable logical identity has been discarded.

The largest unresolved technical question is now whether this minimum trustworthy instrumentation contract remains practical on representative customer production workloads and whether the acquired lineage is sufficiently complete for targeted invalidation.

## Next falsification target

The next experiment should stop asking whether one production-like framework can carry exact causal provenance.

Stage 6C-5 has answered that question for the tested LangChain and PGVector path.

The next falsification target is whether the minimum trustworthy instrumentation contract produces sufficiently complete and useful lineage under a representative application workload.

The question is:

> Can Semantic Drift Engine acquire enough exact causal lineage from a production-like RAG workload to perform targeted invalidation with high affected-asset recall while avoiding unnecessary recomputation?

The next experiment should measure:

- dependency discovery precision
- dependency discovery recall
- affected-asset recall
- invalidation precision
- invalidation recall
- stale-context exposure reduction
- unnecessary reprocessing avoided
- instrumentation burden at each required hook

It should deliberately include:

- unchanged and mutated knowledge units
- historical revisions
- mixed-snapshot retrieval
- multiple revisions of one logical asset
- missing or incomplete provenance
- stale downstream state caused by incomplete lineage

Exact provenance and inferred correspondence must remain separate measurements.

If exact lineage requires invasive instrumentation or still leaves unacceptable invalidation blind spots on a representative workload, the targeted-invalidation product hypothesis must be revised rather than compensated for with heuristic lineage presented as exact.
