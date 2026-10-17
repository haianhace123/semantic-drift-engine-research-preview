BEGIN;

-- Durable scheduling state for continuous monitored-page ingestion.
--
-- crawl_frequency remains scheduling policy.
-- next_crawl_at records durable eligibility for the next crawl.
-- The claim fields form a renewable/recoverable execution lease.
-- Finalization must be fenced by crawl_claim_token.

ALTER TABLE monitored_pages
ADD COLUMN last_crawled_at TIMESTAMPTZ;

ALTER TABLE monitored_pages
ADD COLUMN next_crawl_at TIMESTAMPTZ;

ALTER TABLE monitored_pages
ADD COLUMN crawl_claim_token UUID;

ALTER TABLE monitored_pages
ADD COLUMN crawl_claimed_at TIMESTAMPTZ;

ALTER TABLE monitored_pages
ADD COLUMN crawl_claim_expires_at TIMESTAMPTZ;

ALTER TABLE monitored_pages
ADD CONSTRAINT uq_monitored_pages_organization_entity_url
UNIQUE (
    organization_id,
    entity_id,
    url
);

ALTER TABLE monitored_pages
ADD CONSTRAINT ck_monitored_pages_crawl_claim_state
CHECK (
    (
        crawl_claim_token IS NULL
        AND crawl_claimed_at IS NULL
        AND crawl_claim_expires_at IS NULL
    )
    OR
    (
        crawl_claim_token IS NOT NULL
        AND crawl_claimed_at IS NOT NULL
        AND crawl_claim_expires_at IS NOT NULL
        AND crawl_claim_expires_at > crawl_claimed_at
    )
);

CREATE INDEX ix_monitored_pages_crawl_due
ON monitored_pages (
    next_crawl_at,
    id
)
WHERE is_active IS TRUE;

CREATE INDEX ix_monitored_pages_crawl_claim_expires_at
ON monitored_pages (
    crawl_claim_expires_at
)
WHERE crawl_claim_token IS NOT NULL;

COMMENT ON COLUMN monitored_pages.last_crawled_at IS
'Completion time of the most recent successful scheduled crawl.';

COMMENT ON COLUMN monitored_pages.next_crawl_at IS
'Durable time at which the monitored page becomes eligible for another scheduled crawl.';

COMMENT ON COLUMN monitored_pages.crawl_claim_token IS
'Fencing token identifying the current scheduled-crawl lease holder.';

COMMENT ON COLUMN monitored_pages.crawl_claimed_at IS
'Time at which the current scheduled-crawl lease was acquired.';

COMMENT ON COLUMN monitored_pages.crawl_claim_expires_at IS
'Lease deadline after which another scheduler may reclaim the monitored page.';

COMMIT;
