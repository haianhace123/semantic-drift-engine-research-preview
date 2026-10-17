-- 016_anomaly_window_days.sql
--
-- Persist the activity-window configuration used by deterministic
-- Anomaly v1 detection.
--
-- Existing rows predate deterministic_activity_burst_v1. They receive
-- the v1 default window of 7 days so the column can be made NOT NULL.

ALTER TABLE anomalies
    ADD COLUMN IF NOT EXISTS window_days INTEGER;

UPDATE anomalies
SET window_days = 7
WHERE window_days IS NULL;

ALTER TABLE anomalies
    ALTER COLUMN window_days SET DEFAULT 7;

ALTER TABLE anomalies
    ALTER COLUMN window_days SET NOT NULL;

ALTER TABLE anomalies
    DROP CONSTRAINT IF EXISTS ck_anomalies_window_days_positive;

ALTER TABLE anomalies
    ADD CONSTRAINT ck_anomalies_window_days_positive
    CHECK (window_days >= 1);