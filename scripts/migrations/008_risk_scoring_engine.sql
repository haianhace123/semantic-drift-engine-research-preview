BEGIN;

-- ============================================================
-- RISK SCORING ENGINE V1
--
-- Adds versioned deterministic event-level risk scoring.
--
-- Existing impact_score and risk_level are intentionally kept.
-- Historical events begin in "pending" state and can be scored
-- later once their required vector evidence is available.
-- ============================================================


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_score
DOUBLE PRECISION;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_score_version
VARCHAR(20);


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_score_components
JSON;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_scoring_status
VARCHAR(20)
NOT NULL
DEFAULT 'pending';


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_scoring_error
TEXT;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_scoring_attempts
INTEGER
NOT NULL
DEFAULT 0;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS risk_scored_at
TIMESTAMPTZ;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_semantic_drift_risk_score'
          AND conrelid = 'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT ck_semantic_drift_risk_score
        CHECK (
            risk_score IS NULL
            OR (
                risk_score >= 0.0
                AND risk_score <= 100.0
            )
        );

    END IF;
END
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_semantic_drift_risk_scoring_status'
          AND conrelid = 'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT ck_semantic_drift_risk_scoring_status
        CHECK (
            risk_scoring_status IN (
                'pending',
                'processing',
                'completed',
                'failed'
            )
        );

    END IF;
END
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_semantic_drift_risk_scoring_attempts'
          AND conrelid = 'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT ck_semantic_drift_risk_scoring_attempts
        CHECK (
            risk_scoring_attempts >= 0
        );

    END IF;
END
$$;


CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_risk_scoring_status
ON semantic_drift_events (
    risk_scoring_status
);


CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_risk_score
ON semantic_drift_events (
    risk_score
)
WHERE risk_score IS NOT NULL;


UPDATE semantic_drift_events
SET risk_scoring_status = 'pending'
WHERE risk_scoring_status IS NULL;


UPDATE semantic_drift_events
SET risk_scoring_attempts = 0
WHERE risk_scoring_attempts IS NULL;


DO $$
DECLARE
    invalid_score_count INTEGER;
    invalid_attempt_count INTEGER;
BEGIN

    SELECT COUNT(*)
    INTO invalid_score_count
    FROM semantic_drift_events
    WHERE risk_score IS NOT NULL
      AND (
          risk_score < 0.0
          OR risk_score > 100.0
      );

    IF invalid_score_count > 0 THEN
        RAISE EXCEPTION
            'Risk scoring migration failed: invalid risk scores found.';
    END IF;


    SELECT COUNT(*)
    INTO invalid_attempt_count
    FROM semantic_drift_events
    WHERE risk_scoring_attempts < 0;

    IF invalid_attempt_count > 0 THEN
        RAISE EXCEPTION
            'Risk scoring migration failed: negative attempts found.';
    END IF;

END
$$;


COMMIT;