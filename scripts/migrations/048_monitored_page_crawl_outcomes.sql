-- Record only finalized attempts. A worker with an expired/superseded claim
-- must never overwrite the operational evidence for a newer crawl.
ALTER TABLE monitored_pages
    ADD COLUMN last_crawl_attempt_at TIMESTAMPTZ,
    ADD COLUMN last_crawl_status VARCHAR(16),
    ADD COLUMN last_crawl_failure_code VARCHAR(32),
    ADD COLUMN consecutive_crawl_failures INTEGER NOT NULL DEFAULT 0;

ALTER TABLE monitored_pages
    ADD CONSTRAINT ck_monitored_pages_last_crawl_status
    CHECK (last_crawl_status IS NULL OR last_crawl_status IN ('succeeded', 'failed')),
    ADD CONSTRAINT ck_monitored_pages_failure_code
    CHECK (
        last_crawl_failure_code IS NULL OR last_crawl_failure_code IN (
            'blocked_destination', 'fetch_failed', 'content_rejected',
            'unsupported_source', 'snapshot_unavailable', 'internal_error'
        )
    ),
    ADD CONSTRAINT ck_monitored_pages_outcome_consistency
    CHECK (
        (last_crawl_status IS NULL AND last_crawl_attempt_at IS NULL AND last_crawl_failure_code IS NULL)
        OR
        (last_crawl_status = 'succeeded' AND last_crawl_attempt_at IS NOT NULL AND last_crawl_failure_code IS NULL)
        OR
        (last_crawl_status = 'failed' AND last_crawl_attempt_at IS NOT NULL AND last_crawl_failure_code IS NOT NULL)
    ),
    ADD CONSTRAINT ck_monitored_pages_crawl_failure_count
    CHECK (consecutive_crawl_failures >= 0);
