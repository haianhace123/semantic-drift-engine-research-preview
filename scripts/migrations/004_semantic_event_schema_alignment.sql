BEGIN;

-- ============================================================
-- SEMANTIC DRIFT EVENT SCHEMA ALIGNMENT
--
-- Align databases upgraded through historical migrations with
-- the current SQLAlchemy model and the 000 baseline schema.
--
-- Existing NULL values are normalized before NOT NULL is set.
-- ============================================================


-- ============================================================
-- IMPACT SCORE
-- ============================================================

UPDATE semantic_drift_events
SET impact_score = 0
WHERE impact_score IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN impact_score SET DEFAULT 0;

ALTER TABLE semantic_drift_events
ALTER COLUMN impact_score SET NOT NULL;


-- ============================================================
-- ANALYSIS COUNTS
-- ============================================================

UPDATE semantic_drift_events
SET changes_count = 0
WHERE changes_count IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN changes_count SET DEFAULT 0;

ALTER TABLE semantic_drift_events
ALTER COLUMN changes_count SET NOT NULL;


UPDATE semantic_drift_events
SET chunk_count = 0
WHERE chunk_count IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN chunk_count SET DEFAULT 0;

ALTER TABLE semantic_drift_events
ALTER COLUMN chunk_count SET NOT NULL;


UPDATE semantic_drift_events
SET llm_tokens_used = 0
WHERE llm_tokens_used IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN llm_tokens_used SET DEFAULT 0;

ALTER TABLE semantic_drift_events
ALTER COLUMN llm_tokens_used SET NOT NULL;


-- ============================================================
-- JSON COLLECTIONS
-- ============================================================

UPDATE semantic_drift_events
SET related_events = '[]'::json
WHERE related_events IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN related_events SET DEFAULT '[]'::json;

ALTER TABLE semantic_drift_events
ALTER COLUMN related_events SET NOT NULL;


UPDATE semantic_drift_events
SET entities_affected = '[]'::json
WHERE entities_affected IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN entities_affected SET DEFAULT '[]'::json;

ALTER TABLE semantic_drift_events
ALTER COLUMN entities_affected SET NOT NULL;


-- ============================================================
-- EVENT TIMESTAMPS
-- ============================================================

UPDATE semantic_drift_events
SET detected_at = NOW()
WHERE detected_at IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN detected_at SET DEFAULT NOW();

ALTER TABLE semantic_drift_events
ALTER COLUMN detected_at SET NOT NULL;


UPDATE semantic_drift_events
SET first_seen_at = COALESCE(
    detected_at,
    NOW()
)
WHERE first_seen_at IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN first_seen_at SET DEFAULT NOW();

ALTER TABLE semantic_drift_events
ALTER COLUMN first_seen_at SET NOT NULL;


-- ============================================================
-- BOOLEAN FLAGS
-- ============================================================

UPDATE semantic_drift_events
SET is_anomaly = FALSE
WHERE is_anomaly IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_anomaly SET DEFAULT FALSE;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_anomaly SET NOT NULL;


UPDATE semantic_drift_events
SET is_pattern_part = FALSE
WHERE is_pattern_part IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_pattern_part SET DEFAULT FALSE;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_pattern_part SET NOT NULL;


UPDATE semantic_drift_events
SET is_reviewed = FALSE
WHERE is_reviewed IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_reviewed SET DEFAULT FALSE;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_reviewed SET NOT NULL;


UPDATE semantic_drift_events
SET is_archived = FALSE
WHERE is_archived IS NULL;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_archived SET DEFAULT FALSE;

ALTER TABLE semantic_drift_events
ALTER COLUMN is_archived SET NOT NULL;


COMMIT;