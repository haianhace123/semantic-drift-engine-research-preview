# Scheduled public-source authority correction

## Observed failure

The October 10 local crawler log recorded repeated failures across 64 monitored
pages. A Slack snapshot persistence traceback ended with `Scheduled monitored
page type does not match the crawler page type`. The old scheduler dispatch
selected `SlackCrawler`, `GoogleCrawler`, or `OpenAICrawler` on hostname alone;
those classes used fixed `page_type` values regardless of the authorized
`MonitoredPage.page_type` stored in PostgreSQL. Some other pages returned 403,
404, or failed extraction; this change does not imply those upstream failures
will be resolved.

## Authority decision

All **scheduled customer sources** now use `PublicHTMLCrawler`. That crawler
reads the exact tenant-bound monitored-page record, including `page_type`,
uses the safe pinned transport with HTTPS and redirect validation, persists a
snapshot subject to the existing claim token fence, and invokes semantic
detection after persistence. Hostname does not determine document type or
business identity. Source enrollment and read-only pilot probe use the same
public HTTPS eligibility rules for Slack, Google, OpenAI, and all other hosts.

The older single-purpose crawler modules still exist as standalone manual
utilities but are no longer invoked by the scheduled dispatcher. Their
retirement requires separate operator-invocation compatibility review.

## Acceptance requirements

- Existing `MonitoredPage` records need no automatic mutation or reclassification.
- A scheduled `docs` page under `slack.com` is routed to the same generic
  crawler as `docs.example.org`, preserving the stored classification.
- Tenant, URL and type checks in `src/crawler/persistence.py` remain unchanged.
- Network safety, bounded HTML extraction, immutable snapshot write and claim
  fencing remain enforced by `PublicHTMLCrawler` and its dependencies.
- Controlled live acceptance must demonstrate a reachable, allowlisted public
  HTML page can create a baseline and that a changed page can persist a second
  snapshot without losing semantic analysis evidence.
- Historical invalid, blocked, or deleted URLs will continue to fail; changing
  the dispatcher is not a repair of customer source records.

## Operational notes

Run focused registry/enrollment/probe/persistence tests, full unit tests, and
DB integration tests in the repository's Docker environment before deployment.
Then compare per-page crawl outcomes over a complete schedule window. Do not
interpret `container healthy` or a lower overall failure count as proof that
semantic monitoring and downstream invalidation are complete.
