BEGIN;

-- ============================================================
-- SEMANTIC DRIFT EVENT PRODUCTION MIGRATION
--
-- Adds explicit AI analysis lifecycle state, retry metadata,
-- and database-level idempotency for snapshot pairs.
--
-- Existing drift events are preserved.
-- ============================================================


-- ============================================================
-- ANALYSIS STATUS
--
-- pending    = drift detected, AI analysis not completed
-- processing = AI analysis currently running
-- completed  = AI analysis completed successfully
-- failed     = drift exists, AI analysis failed
-- ============================================================

ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS analysis_status
VARCHAR(20);

UPDATE semantic_drift_events
SET analysis_status = 'completed'
WHERE analysis_status IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN analysis_status SET DEFAULT 'pending';

ALTER TABLE semantic_drift_events
ALTER COLUMN analysis_status SET NOT NULL;

ALTER TABLE semantic_drift_events
ADD CONSTRAINT ck_semantic_drift_event_analysis_status
CHECK (
    analysis_status IN (
        'pending',
        'processing',
        'completed',
        'failed'
    )
);


-- ============================================================
-- ANALYSIS ERROR
--
-- Stores the last safe-to-display failure reason.
-- Do not store API keys, authorization headers, or raw secrets.
-- ============================================================

ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS analysis_error
TEXT;


-- ============================================================
-- ANALYSIS ATTEMPTS
--
-- Number of AI analysis attempts for this event.
-- ============================================================

ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS analysis_attempts
INTEGER NOT NULL DEFAULT 0;

ALTER TABLE semantic_drift_events
ADD CONSTRAINT ck_semantic_drift_event_analysis_attempts
CHECK (analysis_attempts >= 0);


-- ============================================================
-- ANALYZED AT
--
-- NULL while analysis is pending/processing/failed.
-- Set when semantic analysis completes successfully.
-- ============================================================

ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS analyzed_at
TIMESTAMPTZ;


-- ============================================================
-- RISK LEVEL
--
-- Existing records are preserved as-is.
-- New events default to unknown until risk analysis completes.
-- ============================================================

ALTER TABLE semantic_drift_events
ALTER COLUMN risk_level DROP DEFAULT;

ALTER TABLE semantic_drift_events
ALTER COLUMN risk_level SET DEFAULT 'unknown';

ALTER TABLE semantic_drift_events
ALTER COLUMN risk_level SET NOT NULL;


-- ============================================================
-- CONFIDENCE
--
-- A failed/pending AI analysis must not pretend to have a
-- confidence score.
-- ============================================================

ALTER TABLE semantic_drift_events
ALTER COLUMN confidence DROP DEFAULT;


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_analysis_status
ON semantic_drift_events(analysis_status);

CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_risk_level
ON semantic_drift_events(risk_level);

CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_old_snapshot
ON semantic_drift_events(old_snapshot_id);

CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_new_snapshot
ON semantic_drift_events(new_snapshot_id);


-- ============================================================
-- IDEMPOTENCY
--
-- One snapshot transition must produce at most one drift event.
--
-- This protects against:
--
-- crawler retry
-- worker retry
-- duplicated jobs
-- concurrent detector execution
-- application restart
--
-- Existing duplicate pairs must be resolved before this
-- constraint can be created. We intentionally do NOT delete
-- existing data automatically.
-- ============================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT
                old_snapshot_id,
                new_snapshot_id,
                COUNT(*) AS event_count
            FROM semantic_drift_events
            GROUP BY
                old_snapshot_id,
                new_snapshot_id
            HAVING COUNT(*) > 1
        ) duplicate_pairs
    ) THEN
        RAISE EXCEPTION
            'Cannot create semantic drift event idempotency constraint: duplicate snapshot pairs already exist. Resolve duplicates before applying this migration.';
    END IF;
END
$$;


ALTER TABLE semantic_drift_events
ADD CONSTRAINT uq_semantic_drift_event_snapshot_pair
UNIQUE (
    old_snapshot_id,
    new_snapshot_id
);


-- ============================================================
-- BACKFILL EXISTING EVENTS
--
-- Existing events were already persisted by the previous
-- detector implementation. Mark them as completed because
-- they represent already-created drift events.
--
-- Only rows without an explicit status are affected.
-- ============================================================

UPDATE semantic_drift_events
SET analysis_status = 'completed'
WHERE analysis_status IS NULL;


-- ============================================================
-- VALIDATION
-- ============================================================

DO $$
DECLARE
    invalid_status_count INTEGER;
    invalid_attempt_count INTEGER;
BEGIN

    SELECT COUNT(*)
    INTO invalid_status_count
    FROM semantic_drift_events
    WHERE analysis_status NOT IN (
        'pending',
        'processing',
        'completed',
        'failed'
    );

    IF invalid_status_count > 0 THEN
        RAISE EXCEPTION
            'Semantic drift migration validation failed: invalid analysis_status values detected.';
    END IF;


    SELECT COUNT(*)
    INTO invalid_attempt_count
    FROM semantic_drift_events
    WHERE analysis_attempts < 0;

    IF invalid_attempt_count > 0 THEN
        RAISE EXCEPTION
            'Semantic drift migration validation failed: negative analysis_attempts detected.';
    END IF;

END
$$;


COMMIT;