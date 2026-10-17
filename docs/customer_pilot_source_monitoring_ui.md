# Customer pilot: source monitoring workspace

PR.15 connects the public HTML enrollment API introduced in PR.14 to the production React workspace. It does not add another crawler, semantic model, source table, or job scheduler.

## Customer workflow

Open `/dashboard/index.html#sources`, or choose **Sources** from the integrity workspace. An admin or user can enroll a public HTTPS URL, choose a document category and polling interval, then see the new source in the inventory. A viewer can inspect the inventory without mutation controls. Existing authentication cookies and CSRF enforcement apply to all API calls.

The inventory uses `GET /api/sources?limit=25&offset=N`. The list response adds a tenant-scoped snapshot count for each item on the selected page, together with the organization entity name. No page content is returned. `POST /api/sources/{page_id}/frequency` changes the polling interval under the same tenant, authorization and row-lock rules as pause/resume; any active crawl claim causes a conflict rather than concurrent mutation.

## Status contract

The UI explicitly separates ingestion from semantic analysis:

- **Awaiting first snapshot:** no persisted snapshot for the source. This does not identify why a crawl has not completed.
- **Baseline captured:** exactly one snapshot. This does not establish a drift event.
- **Snapshot history available:** at least two snapshots. This does not prove that semantic analysis succeeded.
- **Paused:** the source is not scheduled for further crawls. Previously collected evidence is not deleted.

The last successful crawl timestamp comes from the existing monitored-page field. The API does not yet persist individual per-source crawl errors, so the UI does not invent an error reason or a "healthy" flag. A drift finding is visible in the existing integrity workspace only after its separate analysis pipeline succeeds.

## Security and operational boundaries

No private documents, authenticated pages, JavaScript-rendered pages, or non-HTML documents are supported by this pilot. The enrollment service retains URL validation and source quota enforcement. The generic crawler independently validates DNS and redirects when fetching.

The list query is scoped to the authenticated organization. Snapshot counts are grouped by both organization ID and the monitored page IDs on the requested page. Mutation requests require CSRF and editor/admin privileges. The frontend does not persist authentication tokens or user-provided page content.

## Verification

Run the focused PostgreSQL integration regression in `tests/integration/test_public_source_enrollment_api.py` and the frontend build (`npm ci && npm run build` in `frontend/`). With a new tenant that has no drift events, verify that the workspace still offers **Add or inspect sources**. Verify a viewer can inspect but not change sources, and that changes do not affect another tenant's sources. On a newly enrolled source, the UI must remain at **Awaiting first snapshot** until its actual snapshot appears.

This batch does not certify a public internet crawling success rate or material semantic-change accuracy. Those require an explicitly permitted external source and ground-truth evaluation.
