# Seeded intelligence: downstream exposure decision gate

The October 10 forensic audit of organization `default` returned exactly 60 historical
seeder-shaped sources, 178 of 269 snapshots with synthetic fingerprints and 89 of
89 events with matching synthetic snapshot-pair fingerprints. This establishes
strong evidence that the displayed semantic event history is synthetic, not
measured customer-source detection accuracy.

Do not treat the remaining 91 snapshot rows as independently verified real
observations; they are not covered by this fingerprint test.

## Read-only downstream assessment

Run:

```powershell
docker compose run --rm -v "${PWD}:/app" api `
    python -m scripts.audit_seed_downstream_readonly `
    --organization 08f96bf7-6adb-4b6a-a39e-2379da0de169
```

The script explicitly begins a PostgreSQL read-only transaction, restricts
records by organization ID, and prints aggregate counts. It does not emit user
emails, webhook URLs, API keys, snapshot contents, event IDs, delivery payloads
or other tenant data. It reports direct event-linked anomalies and risk
assessments, patterns whose JSON event IDs include a matching synthetic event,
alerts with direct or typed source references, and delivery outcomes for those
alerts. Separate organization-wide lineage/workflow totals are **not evidence
of causation** from seed data. Missing references in legacy alert records do
not prove an alert is clean; inspect old records before isolation.

## Decision gate

1. Independently verify whether `default` is a development/demo organization and
   who owns its users, sessions, credentials, integrations and live workloads.
2. Check webhook deliveries and downstream lineage/invalidation side effects.
   If anything was externally delivered, determine recipients and remediation
   obligations before changing source status.
3. Confirm the existence and restorability of a dated PostgreSQL backup.
4. Propose a separate, reversible tenant-scoped **monitoring suspension** with
   exact before/after row counts and concurrency protection. No deletion or
   mutation is implemented by the forensic audit.
5. Keep the raw seeded history traceable and distinguish it from verified
   observations in any analytical or customer-facing claim.
6. Independently validate the six other failed sources and at least one real
   source transition end to end before declaring ingestion production-ready.

A clean downstream report does not by itself authorize suspension or deletion.
