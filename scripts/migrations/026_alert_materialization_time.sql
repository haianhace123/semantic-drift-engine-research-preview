BEGIN;

-- ============================================================
-- ALERT MATERIALIZATION TIME
--
-- created_at is the durable knowledge/materialization time of an
-- Alert. Delivery reconciliation traverses Alert history using the
-- stable keyset cursor:
--
--     (created_at, id)
--
-- Historical rows with NULL created_at cannot participate safely in
-- that ordering. Backfill them with one transaction-stable migration
-- timestamp, then enforce the invariant at the database boundary.
-- ============================================================

UPDATE alerts
SET created_at = CURRENT_TIMESTAMP
WHERE created_at IS NULL;

ALTER TABLE alerts
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE alerts
    ALTER COLUMN created_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS
    ix_alerts_org_created_id
ON alerts (
    organization_id,
    created_at ASC,
    id ASC
);

COMMENT ON COLUMN alerts.created_at IS
    'Database materialization time used for deterministic Alert history ordering and delivery reconciliation.';

COMMIT;
