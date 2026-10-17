ALTER TABLE semantic_drift_events
    ADD COLUMN temporal_invalidation_status VARCHAR NOT NULL DEFAULT 'pending',
    ADD COLUMN temporal_invalidation_error TEXT,
    ADD COLUMN temporal_invalidation_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN temporal_invalidation_analyzed_at TIMESTAMPTZ;

ALTER TABLE semantic_drift_events
    ADD CONSTRAINT ck_semantic_drift_events_temporal_invalidation_status
    CHECK (
        temporal_invalidation_status IN (
            'pending',
            'completed',
            'failed'
        )
    );

ALTER TABLE semantic_drift_events
    ADD CONSTRAINT ck_semantic_drift_events_temporal_invalidation_attempts
    CHECK (temporal_invalidation_attempts >= 0);

CREATE INDEX ix_semantic_drift_events_temporal_invalidation_work
    ON semantic_drift_events (
        temporal_invalidation_status,
        temporal_invalidation_attempts,
        detected_at,
        id
    )
    WHERE temporal_invalidation_status IN ('pending', 'failed');