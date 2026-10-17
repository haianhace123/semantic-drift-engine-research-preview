# Public HTML source monitoring: first customer integration boundary

This capability allows an authenticated organization to enroll a public HTML
page in the existing crawler scheduler without inserting rows into PostgreSQL
manually. It is not an external authenticated connector or a browser automation
system.

## Supported contract

- Pages must be public HTTPS documents on public DNS hostnames.
- URL credentials, query strings, fragments and nonstandard ports are rejected.
  URLs containing tokens in a query string should not be enrolled.
- Static HTML or XHTML is supported. JavaScript-only, signed-in, private-network,
  PDF and API responses are out of scope.
- Source-specific adapters remain the authority for the three existing official
  pages: OpenAI Terms of Use, Google Privacy and Slack Terms of Service.
  Their source type cannot be overridden.
- The generic crawler uses the existing DNS-pinned outbound transport, checks
  every redirect and only permits redirects on the enrolled hostname and port
  443. It reads at most 1 MiB of decompressed HTML and rejects normalized text
  outside the 100–100,000-character range. It does not truncate semantic evidence.
- Enrolled sources start active with `next_crawl_at=NULL`, so an available
  crawler scheduler can claim their first observation. It does not imply that
  the first crawl, semantic analysis or risk scoring has completed.
- Creation requires an authenticated `admin` or `user` in the organization;
  `viewer` cannot create, pause or resume sources. Reads are tenant-scoped.
- Organization `quota_urls` is enforced transactionally at enrollment time.
  Repeated enrollment of the same URL produces HTTP 409, not another page.

## API

`POST /api/sources` (include the standard `X-CSRF-Token` header matching the authenticated browser CSRF cookie)

```json
{
  "url": "https://docs.example.org/policies/retention",
  "label": "Retention policy",
  "page_type": "docs",
  "crawl_frequency": "daily"
}
```

`GET /api/sources?limit=100&offset=0` lists only the authenticated tenant's
monitored pages and their last/next crawl timestamps.

`POST /api/sources/{page_id}/pause` and `.../resume` change the scheduling
state while rejecting active claim ownership. Resuming makes the page due
immediately. Source records are retained for temporal history and quota
accounting.

## Pilot operator checks

1. Create a source with an authorized authenticated account.
2. Confirm the source appears in the tenant's source list, initially with no
   `last_crawled_at` value.
3. Check the crawler scheduler heartbeat through the operations API, then
   verify the first snapshot exists before interpreting the source as live.
4. Introduce a permitted revision on a test document and inspect the semantic
   event. Treat provider failures/abstentions as incomplete analysis.
5. Publish exact consumption lineage from a downstream integration; crawling
   a public document alone **does not** reconstruct exact downstream
   dependencies.
6. Verify the resulting impact and invalidation decisions through the
   established Lineage API. Physical recovery still needs trusted downstream
   evidence and separate execution authority.

Do not use this feature to fetch content for which you do not have permission.
It does not check robots.txt or individual website terms automatically. Use
reasonable crawl frequencies and obtain permission where required. No external
web page is fetched at enrollment time.

## Remaining customer-pilot gaps

The current UI does not expose source enrollment; this API is usable by an
integration or authenticated API client. Private sources, PDF ingestion, JS
rendering, historical source replay, crawl health per source and customer-facing
onboarding still require product work. A source can have an admitted URL but
an unsupported document format or unavailable provider, and should not be
presented as fully operational in that state.
