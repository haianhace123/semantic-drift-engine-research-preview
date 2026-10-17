# Materialization dependency diff

## Scope

SDE already stores typed, exact-revision dependencies through immutable lineage observations. This endpoint exposes a read-only difference between two reported materializations of the same source asset. It does not create another graph persistence authority, modify risk scoring, or infer dependencies from entity mentions.

`GET /api/lineage/materialization-dependency-diff`

Required query parameters: `source_external_id`, `asset_external_id`, `from_sequence`, `to_sequence`.
The API requires an API key with `read` permission. Both observations must exist within that API key's organization, must be materializations, and must have strictly increasing sequences. Either missing observation produces a 404. Reversed or equal sequences produce a 422.

Example request (authenticated):

```http
GET /api/lineage/materialization-dependency-diff?source_external_id=policy-source&asset_external_id=policy-index&from_sequence=1&to_sequence=2
```

The response includes source/asset identity, before/after sequence and revision/snapshot identifiers, and three sorted lists: `added`, `removed`, `unchanged`. Each dependency is identified by `(asset_external_id, revision_external_id, dependency_type)`; moving a dependency from revision v1 to v2 counts as a removal plus an addition.

## Semantics and limitations

A removed dependency was present in the earlier materialization report and absent from the later one. It **does not** imply an unobserved real-world relationship ended, nor does it imply that the earlier dependency was invalid at the time. Sequences may be nonconsecutive: the API reports the net difference between the two requested observations, not every intermediate transition. Consumption observations are excluded because they have different lifecycle semantics and cannot report an empty dependency set.

The existing `lineage_observations` and `lineage_observation_dependencies` tables remain the only authority. The entity-mention graph and its topology-shift score are separate and are not promoted to typed causal dependencies by this change.

This batch deliberately does not rewrite legacy semantic drift events or regenerate the 89 historical risk assessments that lack completed semantic analysis. A future topology/risk integration must demonstrate grounded correlation to source and downstream revisions, then be independently qualified before affecting alerts or invalidation decisions.

## Validation

Unit tests cover exact revision/type matching, removal of the final dependency, deterministic ordering, and subject/sequence invariants. PostgreSQL integration covers persisted observations, current-only dependency selection, organization isolation, and transaction-scoped read behavior.
