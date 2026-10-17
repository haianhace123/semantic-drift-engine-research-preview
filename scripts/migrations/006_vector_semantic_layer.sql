BEGIN;

-- ============================================================
-- VECTOR SEMANTIC LAYER
--
-- Adds pgvector-backed semantic representation to historical
-- page snapshots and vector-derived drift metrics to semantic
-- drift events.
--
-- Existing snapshots intentionally remain unembedded. Vector
-- backfill is performed by the application worker/service,
-- never by a schema migration.
-- ============================================================


-- ============================================================
-- PGVECTOR
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================
-- PAGE SNAPSHOT EMBEDDINGS
-- ============================================================

ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding
vector(1536);


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding_model
VARCHAR(255);


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding_dimensions
INTEGER;


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedded_at
TIMESTAMPTZ;


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding_status
VARCHAR(20)
NOT NULL
DEFAULT 'pending';


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding_error
TEXT;


ALTER TABLE page_snapshots
ADD COLUMN IF NOT EXISTS embedding_attempts
INTEGER
NOT NULL
DEFAULT 0;


-- ============================================================
-- SNAPSHOT EMBEDDING CONSTRAINTS
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'ck_page_snapshots_embedding_status'
          AND conrelid =
            'page_snapshots'::regclass
    ) THEN

        ALTER TABLE page_snapshots
        ADD CONSTRAINT
            ck_page_snapshots_embedding_status
        CHECK (
            embedding_status IN (
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
        WHERE conname =
            'ck_page_snapshots_embedding_attempts'
          AND conrelid =
            'page_snapshots'::regclass
    ) THEN

        ALTER TABLE page_snapshots
        ADD CONSTRAINT
            ck_page_snapshots_embedding_attempts
        CHECK (
            embedding_attempts >= 0
        );

    END IF;
END
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'ck_page_snapshots_embedding_dimensions'
          AND conrelid =
            'page_snapshots'::regclass
    ) THEN

        ALTER TABLE page_snapshots
        ADD CONSTRAINT
            ck_page_snapshots_embedding_dimensions
        CHECK (
            embedding_dimensions IS NULL
            OR embedding_dimensions = 1536
        );

    END IF;
END
$$;


-- ============================================================
-- EMBEDDING LOOKUP / WORKER INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS
ix_page_snapshots_embedding_status
ON page_snapshots (
    embedding_status
);


CREATE INDEX IF NOT EXISTS
ix_page_snapshots_embedding_pending
ON page_snapshots (
    crawled_at,
    id
)
WHERE embedding_status IN (
    'pending',
    'failed'
);


CREATE INDEX IF NOT EXISTS
ix_page_snapshots_embedding_model
ON page_snapshots (
    embedding_model
)
WHERE embedding_model IS NOT NULL;


-- ============================================================
-- VECTOR SEARCH INDEX
--
-- HNSW supports cosine-distance nearest-neighbour lookup.
-- Existing NULL vectors are ignored by pgvector.
-- ============================================================

CREATE INDEX IF NOT EXISTS
ix_page_snapshots_embedding_hnsw
ON page_snapshots
USING hnsw (
    embedding vector_cosine_ops
);


-- ============================================================
-- SEMANTIC DRIFT VECTOR METRICS
--
-- semantic_similarity:
--     cosine similarity between old and new snapshot vectors.
--     1.0 = same direction / highly similar.
--
-- semantic_distance:
--     cosine distance between old and new snapshot vectors.
--     Normally 1 - cosine similarity.
--
-- semantic_magnitude:
--     normalized application-level magnitude used by later
--     risk scoring. Kept separate from raw distance so the
--     scoring formula can evolve without destroying evidence.
-- ============================================================

ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS semantic_similarity
DOUBLE PRECISION;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS semantic_distance
DOUBLE PRECISION;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS semantic_magnitude
DOUBLE PRECISION;


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS vector_analysis_status
VARCHAR(20)
NOT NULL
DEFAULT 'pending';


ALTER TABLE semantic_drift_events
ADD COLUMN IF NOT EXISTS vector_analyzed_at
TIMESTAMPTZ;


-- ============================================================
-- VECTOR METRIC CONSTRAINTS
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'ck_semantic_drift_similarity_range'
          AND conrelid =
            'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT
            ck_semantic_drift_similarity_range
        CHECK (
            semantic_similarity IS NULL
            OR (
                semantic_similarity >= -1.0
                AND semantic_similarity <= 1.0
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
        WHERE conname =
            'ck_semantic_drift_distance_range'
          AND conrelid =
            'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT
            ck_semantic_drift_distance_range
        CHECK (
            semantic_distance IS NULL
            OR (
                semantic_distance >= 0.0
                AND semantic_distance <= 2.0
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
        WHERE conname =
            'ck_semantic_drift_magnitude_range'
          AND conrelid =
            'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT
            ck_semantic_drift_magnitude_range
        CHECK (
            semantic_magnitude IS NULL
            OR (
                semantic_magnitude >= 0.0
                AND semantic_magnitude <= 1.0
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
        WHERE conname =
            'ck_semantic_drift_vector_analysis_status'
          AND conrelid =
            'semantic_drift_events'::regclass
    ) THEN

        ALTER TABLE semantic_drift_events
        ADD CONSTRAINT
            ck_semantic_drift_vector_analysis_status
        CHECK (
            vector_analysis_status IN (
                'pending',
                'processing',
                'completed',
                'failed'
            )
        );

    END IF;
END
$$;


-- ============================================================
-- VECTOR ANALYSIS INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_vector_analysis_status
ON semantic_drift_events (
    vector_analysis_status
);


CREATE INDEX IF NOT EXISTS
ix_semantic_drift_events_semantic_magnitude
ON semantic_drift_events (
    semantic_magnitude
)
WHERE semantic_magnitude IS NOT NULL;


-- ============================================================
-- VALIDATION
-- ============================================================

DO $$
DECLARE
    vector_extension_count INTEGER;
    invalid_snapshot_status_count INTEGER;
    invalid_event_status_count INTEGER;
BEGIN

    SELECT COUNT(*)
    INTO vector_extension_count
    FROM pg_extension
    WHERE extname = 'vector';

    IF vector_extension_count <> 1 THEN
        RAISE EXCEPTION
            'Vector semantic migration failed: pgvector extension is not installed.';
    END IF;


    SELECT COUNT(*)
    INTO invalid_snapshot_status_count
    FROM page_snapshots
    WHERE embedding_status NOT IN (
        'pending',
        'processing',
        'completed',
        'failed'
    );

    IF invalid_snapshot_status_count > 0 THEN
        RAISE EXCEPTION
            'Vector semantic migration failed: invalid page snapshot embedding status.';
    END IF;


    SELECT COUNT(*)
    INTO invalid_event_status_count
    FROM semantic_drift_events
    WHERE vector_analysis_status NOT IN (
        'pending',
        'processing',
        'completed',
        'failed'
    );

    IF invalid_event_status_count > 0 THEN
        RAISE EXCEPTION
            'Vector semantic migration failed: invalid drift event vector analysis status.';
    END IF;

END
$$;


COMMIT;