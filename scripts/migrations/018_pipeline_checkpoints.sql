BEGIN;

-- ============================================================
-- PIPELINE CHECKPOINTS
--
-- Durable organization-scoped checkpoints for asynchronous
-- intelligence pipeline stages.
--
-- Pattern and Anomaly detection operate over organization-level
-- temporal windows rather than individual SemanticDriftEvents.
-- A durable checkpoint is therefore required so that:
--
--   1. successful zero-result detection runs are remembered;
--   2. worker restarts do not lose pending organization work;
--   3. Pattern and Anomaly stages can advance independently;
--   4. processing-time and temporal/knowledge-time remain
--      explicitly separated.
--
-- source_created_at:
--     Materialization-time watermark of the upstream source.
--     For Pattern/Anomaly v1 this is RiskAssessment.created_at.
--
-- source_record_id:
--     Stable tie-breaker for upstream rows sharing the same
--     source_created_at value.
--
-- knowledge_cursor:
--     Temporal cursor used by the deterministic detector.
--     For Pattern/Anomaly v1 this is derived from
--     RiskAssessment.assessed_at.
--
-- processed_at:
--     Wall-clock time when this pipeline checkpoint was most
--     recently completed successfully.
--
-- metadata_json:
--     Optional non-authoritative execution metadata such as
--     detector configuration or evidence/result counts.
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (

    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    pipeline_name VARCHAR(64)
        NOT NULL,

    source_created_at TIMESTAMPTZ
        NOT NULL,

    source_record_id UUID
        NOT NULL,

    knowledge_cursor TIMESTAMPTZ
        NOT NULL,

    processed_at TIMESTAMPTZ
        NOT NULL
        DEFAULT NOW(),

    metadata_json JSONB
);


-- Exactly one durable watermark per organization and pipeline stage.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_pipeline_checkpoints_org_pipeline
ON pipeline_checkpoints (
    organization_id,
    pipeline_name
);


-- Common worker lookup for all checkpoints owned by one tenant.
CREATE INDEX IF NOT EXISTS
    ix_pipeline_checkpoints_organization_id
ON pipeline_checkpoints (
    organization_id
);


-- Useful for operational inspection and stale-stage diagnostics.
CREATE INDEX IF NOT EXISTS
    ix_pipeline_checkpoints_pipeline_processed_at
ON pipeline_checkpoints (
    pipeline_name,
    processed_at DESC
);


-- Upstream materialization watermark inspection.
CREATE INDEX IF NOT EXISTS
    ix_pipeline_checkpoints_org_source_created_at
ON pipeline_checkpoints (
    organization_id,
    source_created_at DESC
);


COMMIT;