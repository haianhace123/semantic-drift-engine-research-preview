# Controlled live semantic pilot: evidence acceptance (PR.20)

## Purpose

This is an operator-run acceptance procedure for a **customer-authorized, public HTTPS document**. It uses the existing React source enrollment, crawler scheduler, snapshot storage, semantic detector, and source diagnostics API. There is no second inference path. The verifier (`scripts.verify_live_semantic_pilot`) **only reads** PostgreSQL and the two latest snapshot artifacts. It does not crawl, write to PostgreSQL, call DeepSeek, or print document contents.

PR.19 established that SDE can fetch and normalize the public RFC 2119 page. It did not establish that a real changed document is detected, analyzed, or useful to a customer. This procedure requires a controlled source that the operator is authorized to publish and edit. It does not create such a source or bypass HTTPS destination restrictions. The source must use an ordinary public hostname; private IPs, localhost, credentials in URLs, and unapproved third-party documents are not suitable.

**Provider transfer approval is a prerequisite, not a CLI switch.** A second crawl with changed content can cause the production detector to send changed excerpts to the configured DeepSeek endpoint. Do not start the material/nonmaterial changes until the test document is explicitly approved for that transfer, the test/development provider credentials are configured, and any cost exposure is acceptable. The verifier itself makes no provider calls.

## Procedure

1. Publish an innocuous HTML test policy on an HTTPS hostname you control. The text should be 100–100,000 normalized characters and the HTML response must fit within 1 MiB. Do not use customer secrets. Use the PR.19 probe (`scripts.probe_public_html_source`) to verify admission, fetch eligibility, and short-term stability before enrollment.
2. Enroll it through the React Source Monitoring workspace as an `hourly` public `docs` source within a **test organization**. Record its organization UUID and source UUID privately. Do not substitute an unscoped URL lookup for the source UUID.
3. Let the normal crawler scheduler execute its first **due** crawl. Check Source Monitoring for `succeeded` and at least one snapshot. The verifier will fail closed if the snapshot file is missing or its SHA-256 differs from `content_hash` in PostgreSQL.
4. Take the baseline checkpoint, storing the returned `latest_snapshot_id`:

   ```powershell
   $org = "<test-organization-uuid>"
   $source = "<enrolled-source-uuid>"
   docker compose run --rm -v "${PWD}:/app" api python -m scripts.verify_live_semantic_pilot `
       --organization-id $org --source-id $source --phase baseline
   ```

5. Change **only this published test document**. For a positive material case, use an explicit rule such as “Data is retained for 30 days” becoming “Data is retained for 90 days”. Do not alter the test organization or its URL. Wait for the next scheduler crawl to be due and complete. Do not change `next_crawl_at` manually, bypass claim fencing, or call the provider through an alternative script.
6. Check that the immediately preceding snapshot is the recorded baseline. Only then run:

   ```powershell
   $baseline = "<latest_snapshot_id-from-baseline>"
   docker compose run --rm -v "${PWD}:/app" api python -m scripts.verify_live_semantic_pilot `
       --organization-id $org --source-id $source --phase changed `
       --baseline-snapshot-id $baseline --expect material
   ```

7. Independently inspect the changed clause in the authenticated temporal workspace. Verify that the displayed old/new evidence is factually correct and useful. The verifier can attest that the persisted semantic result contains one or more changes; **it cannot prove that the model interpretation is correct**. Record observed mistakes and whether the change is actionable.
8. For a separate negative control, take a **new baseline**, perform a meaning-preserving paraphrase or formatting change, wait for the next scheduled crawl, and invoke `--expect nonmaterial` (content changed but `changes_count=0`) or `--expect unchanged` (normalized content hash unchanged). These expectations are distinct; do not report formatting noise as a model false positive if normalization removed it before inference.

The tool returns exit code `0` only if that checkpoint's evidence conditions match the declared expectation, `2` for missing/mismatched evidence, or `3` for an unexpected read failure. A `pass` means *persisted pipeline evidence satisfies the operator's declared expectation*, **not** measured semantic precision/recall or a production readiness certification. The report includes no document text, raw provider response, raw exception, or database URL.

## Failure interpretation

| Reason | Meaning / next action |
| --- | --- |
| `source_not_found` | Confirm test organization and source UUID; no cross-tenant evidence is returned. |
| `awaiting_baseline` / `awaiting_new_snapshot` | Wait for a scheduled crawl; check crawler status and retry diagnostics. |
| `snapshot_artifact_not_verified` | Inspect the runtime snapshot volume and storage retention. A DB row alone is insufficient evidence. |
| `baseline_not_direct_predecessor` | Another snapshot intervened. Start a new baseline; never compare non-adjacent versions as if they were a single change. |
| `expected_content_change_missing` | The cleaner produced identical content, or the published edit was not fetched. |
| `semantic_event_missing` | Read PR.16 source diagnostics; use PR.18 bounded reconciliation only under its documented, separately enabled operational controls. |
| `semantic_analysis_not_completed` | Inspect safe source diagnostics and recovery status; never interpret `pending` or `failed` as a low-risk finding. |
| `semantic_evidence_incoherent` | Treat the event as unverified. Investigate persistence/contract integrity. |
| `semantic_materiality_expectation_mismatch` | Compare the model classification against the controlled ground truth manually. Record as a possible false positive or false negative. |
| `attestation_internal_error` | Operational read failure; diagnose privately without leaking secrets or document text. |

## Evidence limits

The verifier checks the **latest adjacent pair only**, file SHA-256, tenant-scoped event identity, and completed payload consistency. It does not prove snapshot storage disaster recovery, browser user interaction, exact downstream lineage, content quality, provider billing cost, SLA latency, or absence of historical missed events. Do not report `llm_tokens_used=0` as a measured provider cost. A controlled one-document test is not an independent annotated holdout: for semantic quality, use the separately documented externally labeled holdout process and preserve failures/abstentions in the denominator.

This is a development/customer-pilot diagnostic, not an unattended production job. Do not commit organization IDs, snapshot IDs linked to a customer, operator results, private test documents, provider credentials, or workspace screenshots containing confidential information.
