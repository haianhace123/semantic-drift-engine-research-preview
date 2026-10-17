# Historical seed-data provenance and pilot admission

## Observed state, 2026-10-10

The read-only production-like local inventory returned 66 active monitored sources
across seven organizations. One organization had 60 sources with URLs formatted
exactly as `https://<entity.domain>/<page_type>`; 57 of those had failed crawl
outcomes, two had succeeded and one had no recorded outcome. The other six
organizations each had a single source with a failed outcome.

All 60 shape matches are in organization
`08f96bf7-6adb-4b6a-a39e-2379da0de169`. The historical utility created
20 entities and three pages per entity from this precise URL template. It also
manufactured snapshots, drift events, pattern observations, anomalies and
alerts with random risk/confidence attributes. **URL shape is evidence, not proof
of provenance or permission to change/delete customer records.**

Failure codes over the whole 66-source inventory were:
`blocked_destination: 43`, `fetch_failed: 16`, and `content_rejected: 4`.
`blocked_destination` includes policy and redirect-host failures; it does not
imply 43 malicious URLs.

## Read-only corroboration

Run the following in the local API container. It explicitly scopes all queries
to the target organization, reports aggregate evidence fingerprints, and does
not print raw snapshot text, billing addresses, API keys or secrets:

```powershell
# Copy scripts/audit_seed_provenance_readonly.py into the repository first.
docker compose run --rm -v "${PWD}:/app" api `
    python -m scripts.audit_seed_provenance_readonly `
    --organization 08f96bf7-6adb-4b6a-a39e-2379da0de169
```

The script checks: the exact 20-domain / three-distinct-page-type source
matrix; hashes and seeded file-path patterns of the original synthetic
old/new texts; exact synthetic event old/new hash pairs and summaries; and
counts of persisted alerts, anomalies and patterns. A mismatch is reported,
not repaired. A positive match is strong corroboration but not formal proof
of ownership; require operator assessment of organization records and audit
history before a tenant-scoped action.

## Quarantine decision gate (not automated)

1. Verify the organization's owner, slug, memberships and purpose. Confirm no
   paying user or current pilot depends on the monitored pages or records.
2. Take and verify a recoverable database backup before any action.
3. Determine whether synthetic events feed alert deliveries, lineage impact,
   invalidation decisions, verification records, integrations or analytics.
   Historical downstream records must not be silently deleted or rewritten.
4. If verified as an isolated development/demo tenant, suspend its monitoring
   using a reviewable, reversible, tenant-scoped operation with a record of
   exactly which pages changed. Never stop/delete other organizations.
5. Re-run inventory and confirm live crawler attempts and failure metrics for
   remaining real sources independently. All six non-matching sources in the
   original inventory also had failed outcomes; the hotfix alone has not
   established a viable real-world customer ingestion cohort.
6. For pilot acceptance, enroll a verified real public HTML source, capture
   two legitimate immutable observations, detect an actual semantic change,
   inspect evidence provenance, and verify downstream impact/notifications
   before claiming an end-to-end product win.

## Source hygiene

`scripts/seed_demo_data.py` had no runtime imports or Compose invocation in
the reviewed repository. It was retired to prevent accidentally injecting
fabricated operational evidence into shared tenant databases. Removing this
script does **not** remove historical seeded records or their derived effects.
Do not reconstruct those records from live monitoring as though they had been
observed; preserve explicit provenance and reversible remediation.
