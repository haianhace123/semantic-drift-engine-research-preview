BEGIN;

-- ============================================================
-- VECTOR ANALYSIS LIFECYCLE HARDENING
--
-- Adds operational state required by VectorAnalysisService.
-- Migration 006 established vector metrics/status; this
-- migration adds retry/error observability without rewriting
-- already-applied migration history.
-- ============================================================


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS vector_analysis_error
TEXT;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS vector_analysis_attempts
INTEGER
NOT NULL
DEFAULT 0;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'ck_semantic_drift_vector_analysis_attempts'
          AND conrelid =
            'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT
            ck_semantic_drift_vector_analysis_attempts
        CHECK (
            vector_analysis_attempts >= 0
        );

    END IF;
END
$$;


-- Existing historical events have never undergone a real
-- vector-analysis attempt, so zero is the correct backfill.
UPDATE semantic_drift_events
SET vector_analysis_attempts = 0
WHERE vector_analysis_attempts IS NULL;


DO $$
DECLARE
    invalid_attempt_count INTEGER;
BEGIN

    SELECT COUNT(*)
    INTO invalid_attempt_count
    FROM semantic_drift_events
    WHERE vector_analysis_attempts < 0;

    IF invalid_attempt_count > 0 THEN
        RAISE EXCEPTION
            'Vector lifecycle migration failed: negative analysis attempts found.';
    END IF;

END
$$;


COMMIT;