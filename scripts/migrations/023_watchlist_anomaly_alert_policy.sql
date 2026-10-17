ALTER TABLE watchlists
ADD COLUMN IF NOT EXISTS alert_on_anomaly BOOLEAN;

UPDATE watchlists
SET alert_on_anomaly = TRUE
WHERE alert_on_anomaly IS NULL;

ALTER TABLE watchlists
ALTER COLUMN alert_on_anomaly SET DEFAULT TRUE;

ALTER TABLE watchlists
ALTER COLUMN alert_on_anomaly SET NOT NULL;

COMMENT ON COLUMN watchlists.alert_on_anomaly IS
    'Whether this Watchlist permits anomaly-derived alerts when its scope matches.';