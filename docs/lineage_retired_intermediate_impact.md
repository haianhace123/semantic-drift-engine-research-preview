# Impact traversal through retired intermediate revisions

A downstream asset can continue to consume a revision that is no longer the
latest materialization of its upstream asset. Repairing the upstream asset
does not implicitly repair all assets that consumed its older revision.

For example:

- `policy@v1` was consumed by `index-a@v1`.
- `index-a@v2` is now current and consumes `policy@v2`.
- `index-b@v1` is still current and consumes `index-a@v1`.

An impact query rooted at `policy@v1` must return `index-b@v1` with causal path
`policy@v1 -> index-a@v1 -> index-b@v1`. It must not return retired
`index-a@v1` as a current actionable target. When `index-b@v2` replaces
`index-b@v1` with a safe dependency, the query must no longer return it.

The impact service traverses exact recorded dependencies, including historical
intermediate revisions, while exposing only revision identities found in the
current lineage observation for their subject. This distinction applies to
the impact query and its downstream decision derivation; execution eligibility
still evaluates the latest materialization and active policy at execution time.

An observation establishes provenance provided by the producer; it does not
verify semantic correctness or establish that a physical downstream system
was actually repaired. The implementation does not infer dependencies from
entity mentions or vector similarity.

Tests cover a retired intermediate with an unrepaired current downstream
index, subsequent downstream repair, cycles, and ordinary current-only cases.
