BEGIN;

-- Continuous ingestion requires every monitored page to have an explicit
-- crawl frequency. Existing data was audited before this migration and
-- contained no NULL crawl_frequency values.

ALTER TABLE monitored_pages
ALTER COLUMN crawl_frequency SET NOT NULL;

COMMIT;