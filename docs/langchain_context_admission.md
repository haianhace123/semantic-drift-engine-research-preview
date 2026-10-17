# LangChain request-time context admission

`query_langchain_context_integrity` returns factual impact evidence. It does
not authorize an application to serve retrieved documents. Call the admission
boundary before passing any retrieved content to a model or agent:

```python
from src.integrations.langchain import admit_langchain_context

admitted = admit_langchain_context(
    client=integrity_client,
    serving_revision=serving_revision,
    retrieved_documents=retrieved_documents,
)
answer = model.invoke(format_context(admitted.documents))
```

The default `unknown="block"` policy rejects revisions without a recorded
decision. Every unresolved impact also blocks, regardless of the unknown
policy. A `verified` integrity status is **execution/rematerialization
evidence for an invalidated target**, not proof that the same historical
target revision is now safe to serve. The guard blocks that old revision with
`superseded_revision` even after its repair workflow completes. Admit a
successor revision only after its own exact source/index/chunk identities
receive an independent allowed integrity assessment. Network, HTTP, malformed provenance, and protocol errors are not
converted to approval or a fallback context. Handle them as unavailable or
requiring review, not as permission to use the original retrieval result.

Applications may explicitly opt into a different caller-owned policy with
`policy=IntegrityAdmissionPolicy(unknown="proceed")`. This is a risk decision,
not a claim that the content is verified. In the current control-plane model,
`unknown` means there is no persisted invalidation decision for the requested
revision, while `not_affected` means a recorded no-action decision. Neither
means all knowledge is globally current or accurate.

The guard evaluates exact serving index and retrieved chunk identities at
request time. It does not automatically gate arbitrary downstream model calls
or make querying SDE atomic with a vector-store read. The consumer must use
only documents returned by the guard; fail-open exception handlers bypass the
contract. Adopt separately verified indexing provenance and recheck context
for every generation operation.

## Content-addressed chunks and byte integrity

For revisions emitted by SDE's LangChain indexing adapter, the revision ID is
`<chunk-id>:<first-16-lowercase-hex-of-SHA256(UTF8(page_content))>`. The
request-time adapter now checks this digest against the retrieved bytes **before**
querying SDE. An unchanged revision ID attached to different content is refused,
even if the server reports `not_affected`. This is a local consistency check:
the 16-hex digest is truncated and is not an independently signed attestation.

Other integrations may use opaque external revision IDs. Those remain
identity-only; SDE cannot derive their expected content bytes from an opaque ID.
They need independent materialization/byte evidence when that assurance is
required. This check does not establish that a source is globally current or
that a source change cannot happen immediately after admission.

`admit_langchain_context` now snapshots document ID, text, and a shallow copy of
metadata **before** querying the integrity service. `admitted.documents` contains
frozen, detached document snapshots, not original LangChain `Document` objects.
Do not pass the original retrieval result to the model. Do not assume that
nested non-provenance metadata values have been recursively frozen. This is a
breaking object-identity change: consumers relying on `is` or mutating admitted
documents must migrate to the returned immutable fields. For an authority-backed,
byte-verified canonical recovery flow, use the separately scoped pilot recovery
admission mechanism, not the general identity-only gate.
