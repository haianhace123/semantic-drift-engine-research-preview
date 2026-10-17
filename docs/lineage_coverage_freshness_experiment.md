# P2.8A: Lineage coverage and source-freshness falsification

**Status:** offline experiment; no runtime contract, database migration, API change, or claim of detection accuracy.

## Production behavior being challenged

`src/services/lineage_target_integrity_service.py::_assess_target_from_decisions()` produces `not_affected` when historical decisions exist and all are `no_action`. `src/integrations/integrity_admission.py::evaluate_integrity_admission()` then permits the context by default. These decisions do **not** contain an exact upstream manifest, a completion record for source-event processing, or a bounded freshness window.

This is a limitation of the present contract, **not evidence of a production breach**. Existing integrations already refuse unknown integrity and refuse many partial PGVector acknowledgements. The negative controls here should not be read as a claim that current LangChain onboarding accepts partial physical writes.

## What the bounded experiment does

`benchmarks/temporal_knowledge_integrity/coverage_freshness_experiment.py` uses the repository's actual `LineageObservation` and `LineageRevisionReference` types for a synthetic index publication. It compares:

- a coarse `not_affected`/`affected`/`verified`/`unknown` admission approximation, *not a live HTTP call*;
- a **hypothetical** strict scoped window with a separately trusted producer input manifest, exact physical ACK and persisted observations, committed publisher state, a trusted source event cursor, a completed processing checkpoint and a `no_action` decision through that cursor.

Execute without provider or database access:

```powershell
.\.venv-sde\Scripts\python.exe -m pytest -q tests/unit/test_lineage_coverage_freshness_experiment.py
.\.venv-sde\Scripts\python.exe -m benchmarks.temporal_knowledge_integrity.coverage_freshness_experiment
```

The synthetic cases include: complete publication; completed relevant source event; unrelated source event; missing observed chunk; partial physical acknowledgement; partially committed publication; new event not yet processed; a decision older than the latest event; `unknown`, `affected`, and `verified` controls; and an omitted upstream dependency which the producer **also omitted** from its own input manifest.

### Observed in the offline experiment

| Result (12 synthetic controls) | Coarse status model | Hypothetical strict window |
|---|---:|---:|
| Stale scenarios admitted | 6 | 1 |
| Safe scenarios blocked | 0 | 0 |

**Do not treat these as recall, false-positive rate, service-level objectives, or market evidence.** Scenario truths are deliberately supplied by the test author. Existing production may already reject some of the modeled partial-write cases for independent reasons. The experiment measures proposed protocol behavior only.

### Proof of an impossibility boundary

`complete_exact_revision` and `unobserved_upstream_omitted_by_producer` have **identical admissible evidence and identical algorithmic output**, but different external truth. Even a perfectly implemented comparison of published/observed/acknowledged manifests cannot detect a dependency that no trusted producer input ever identified. A client-supplied `complete=true` flag or digest calculated over the already incomplete published set cannot fix this.

Therefore **do not market or expose global `lineage_complete` / `knowledge_safe` claims** on the basis of this protocol alone. A trustworthy upstream enumeration authority and materialization input contract are prerequisites, with scope and limits disclosed.

## Required authority and invariants before runtime work

1. **Materialization authority:** exact tenant, source/snapshot, index and chunk revision identities; deterministic, separately committed *expected* dependency manifest from trusted writer inputs. Distinguish an empty valid manifest from unobserved dependencies. Publisher assertions alone are not sufficient; physical write ACKs and SDE observation persistence must reconcile against the expected manifest.
2. **Source-event authority:** an organization- and source-scoped ordered event cursor tied to durable source event publication; never use a globally incremented unrelated cursor to block this source unnecessarily.
3. **Processing authority:** durable completion through that cursor, including a corresponding decision watermark. A `no_action` record at an older cursor is not current. Reject inconsistent, untrusted or rolled-back cursor claims.
4. **Admission authority:** use the exact context/index revision, policy semantics and scoped source cursor to choose allow/block/unknown. Existing `affected`/`verified`/`unknown` stay fail-closed under strict policy. Readmission of a successor requires its own proof; old revisions do not become safe after recovery.
5. **Atomicity:** PostgreSQL authority, source crawler, PGVector and downstream workers do not share one atomic transaction. Certificates must name a bounded validity window and be revocable/rechecked when a newer source event is published. The experiment does not close the race between receiving an event and consuming a cached decision.
6. **Tenant boundary:** never reuse evidence across organizations or index revisions. The experiment rejects identity mismatches, but real enforcement must use server-derived tenancy and DB-scoped queries, not client-declared UUIDs.

## Decision and next bounded batch

**P2.8A result: protocol is falsifiable but insufficient for general coverage certification.** Do not add a global boolean certificate or make all existing `not_affected` responses block based on this experiment. That would create availability risk and false confidence at once.

If pursuing **P2.8B**, first constrain its pilot to the SDE-operated LangChain PGVector writer (source materialization provenance + an independent expected input manifest). Validate durability and provenance of publisher inputs, prove tenant- and source-scoped cursor completion on disposable PostgreSQL, then introduce a separate **shadow-only** evaluation on old/new decisions. Acceptance must include deliberate upstream-omission cases and a stated inability to certify uninstrumented sources. Only a later rollout could use a strict opt-in policy when the metadata really exists.

This work does not change the current release gate: independent encrypted off-host backup (R0.13) and guarded deployment remain prerequisites to a production cutover.
