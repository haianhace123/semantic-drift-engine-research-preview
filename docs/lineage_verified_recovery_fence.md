# Post-execution lineage verification evidence fences

A successful downstream invalidation is not the same as a verified repair.
Verification continues to require an explicit result-to-materialization binding
from a trusted integration that attests its own physical repair. The control
plane cannot infer real-world repair from lineage observations alone.

The publication authority now refuses evidence that is already present when
its execution result is recorded. The observation must be a new target
materialization revision ingested strictly after result publication and must
still be the latest materialization for that target when the binding is created.
The target advisory lock serializes verification publication and materialization
persistence. Existing verification bindings remain immutable/idempotent.

Read paths fail closed if a previously stored binding does not meet these
conditions, or if a newer materialization supersedes the bound evidence. The
control-plane verification view reports `awaiting_evidence` in these cases and
retains the bound observation ID for audit. The target integrity query never
promotes such a binding to `verified`. This may downgrade historical labels
on read without rewriting historical rows, intentionally.

Observation `created_at` is database ingestion time, not producer event time.
Sequence and revision identity are producer claims. These are **necessary**
rejection fences, not sufficient causal proof of physical rebuild; producers
must attest that the observed materialization actually describes a repaired
asset. A newly published observation with fabricated content remains outside
the control plane's ability to independently verify.

No database schema change, backfill, rewriting of executions, or physical
adapter side effect is performed by this batch.
