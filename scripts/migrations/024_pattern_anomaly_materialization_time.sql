BEGIN;

-- ============================================================
-- PATTERN + ANOMALY MATERIALIZATION TIME
--
-- detected_at is effective time: the temporal analysis cursor at
-- which the intelligence was detected.
--
-- created_at is knowledge time: the database materialization time
-- at which the persisted intelligence became visible to downstream
-- pipelines.
--
-- Existing rows predate knowledge-time tracking. They are assigned
-- one migration bootstrap timestamp rather than copying detected_at,
-- which would incorrectly reinterpret effective time as knowledge
-- time.
-- ============================================================

-- Capture one stable bootstrap timestamp for all historical rows
-- touched by this migration.
CREATE TEMP TABLE pattern_anomaly_materialization_bootstrap (
    materialized_at TIMESTAMPTZ NOT NULL
) ON COMMIT DROP;

INSERT INTO pattern_anomaly_materialization_bootstrap (
    materialized_at
)
VALUES (
    CURRENT_TIMESTAMP
);


-- ============================================================
-- PATTERNS
-- ============================================================

ALTER TABLE patterns
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;

UPDATE patterns
SET created_at = (
    SELECT materialized_at
    FROM pattern_anomaly_materialization_bootstrap
)
WHERE created_at IS NULL;

ALTER TABLE patterns
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE patterns
    ALTER COLUMN created_at SET NOT NULL;

COMMENT ON COLUMN patterns.created_at IS
    'Knowledge/materialization time when this Pattern became visible in persistent storage. Distinct from effective-time detected_at.';

CREATE INDEX IF NOT EXISTS
    ix_patterns_org_created_id
ON patterns (
    organization_id,
    created_at DESC,
    id DESC
);


-- ============================================================
-- ANOMALIES
-- ============================================================

ALTER TABLE anomalies
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;

UPDATE anomalies
SET created_at = (
    SELECT materialized_at
    FROM pattern_anomaly_materialization_bootstrap
)
WHERE created_at IS NULL;

ALTER TABLE anomalies
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE anomalies
    ALTER COLUMN created_at SET NOT NULL;

COMMENT ON COLUMN anomalies.created_at IS
    'Knowledge/materialization time when this Anomaly became visible in persistent storage. Distinct from effective-time detected_at.';

CREATE INDEX IF NOT EXISTS
    ix_anomalies_org_created_id
ON anomalies (
    organization_id,
    created_at DESC,
    id DESC
);

COMMIT;