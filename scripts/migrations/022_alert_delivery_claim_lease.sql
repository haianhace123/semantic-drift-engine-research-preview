-- Durable claim lease for alert delivery workers.
--
-- claimed_at records ownership time for a processing delivery.
-- It is intentionally separate from last_attempt_at so worker
-- crash recovery does not overload HTTP attempt semantics.

ALTER TABLE alert_deliveries
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_alert_deliveries_processing_claimed_at
    ON alert_deliveries (claimed_at, id)
    WHERE status = 'processing';