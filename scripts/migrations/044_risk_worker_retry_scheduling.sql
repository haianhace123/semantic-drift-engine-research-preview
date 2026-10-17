BEGIN;

ALTER TABLE semantic_drift_events
    ADD COLUMN risk_retry_after TIMESTAMPTZ;

CREATE INDEX ix_semantic_drift_events_risk_retry_after
    ON semantic_drift_events (risk_retry_after)
    WHERE risk_retry_after IS NOT NULL;

COMMIT;
