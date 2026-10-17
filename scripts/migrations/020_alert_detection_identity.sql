BEGIN;

-- ============================================================
-- ALERT DETECTION IDENTITY
--
-- Production Alert generation must be deterministic and
-- idempotent before any external delivery is attempted.
--
-- Legacy Alert rows remain valid and may keep NULL values for
-- the new identity fields.
--
-- event_id:
--     Optional event-centric context. Risk alerts may retain
--     a direct SemanticDriftEvent reference, while Pattern and
--     Anomaly alerts are not forced to point at an arbitrary
--     event.
--
-- source_type:
--     Upstream intelligence source that produced the Alert.
--     Initial production values:
--
--         risk
--         pattern
--         anomaly
--
-- source_id:
--     Identifier of the upstream RiskAssessment, Pattern or
--     Anomaly row. This is intentionally not a foreign key
--     because the source is polymorphic.
--
-- detection_key:
--     Deterministic hash derived from stable alert evidence.
--     New production-generated alerts must always provide it.
--
-- occurred_at:
--     Temporal time of the intelligence signal represented by
--     the Alert. This remains separate from created_at, which
--     represents materialization time.
-- ============================================================


ALTER TABLE alerts
    ALTER COLUMN event_id DROP NOT NULL;


ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS source_type VARCHAR(32);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS source_id UUID;

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS detection_key VARCHAR(64);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ;


-- Deterministic identity for newly generated production Alerts.
--
-- Legacy Alert rows are intentionally excluded because their
-- detection_key is NULL.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_alerts_org_type_detection_key
ON alerts (
    organization_id,
    alert_type,
    detection_key
)
WHERE detection_key IS NOT NULL;


-- Efficient source -> Alert lookup.
CREATE INDEX IF NOT EXISTS
    ix_alerts_org_source
ON alerts (
    organization_id,
    source_type,
    source_id
)
WHERE source_type IS NOT NULL
  AND source_id IS NOT NULL;


-- Primary Alert workspace/read-model path.
CREATE INDEX IF NOT EXISTS
    ix_alerts_org_created_at
ON alerts (
    organization_id,
    created_at DESC,
    id DESC
);


-- Pending Alert processing/delivery lookup.
CREATE INDEX IF NOT EXISTS
    ix_alerts_org_status_created_at
ON alerts (
    organization_id,
    status,
    created_at ASC,
    id ASC
);


-- Temporal Alert replay/query path.
CREATE INDEX IF NOT EXISTS
    ix_alerts_org_occurred_at
ON alerts (
    organization_id,
    occurred_at DESC,
    id DESC
)
WHERE occurred_at IS NOT NULL;


COMMIT;