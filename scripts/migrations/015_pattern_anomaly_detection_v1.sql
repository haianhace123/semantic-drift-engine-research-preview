BEGIN;

-- ============================================================
-- PATTERN + ANOMALY DETECTION V1
--
-- Adds deterministic persistence identity for Pattern/Anomaly
-- detection without rewriting or deleting legacy rows.
--
-- Legacy rows may have NULL detection_key.
-- New deterministic detectors must always persist a
-- non-NULL detection_key.
--
-- Partial unique indexes provide concurrency-safe idempotency
-- for new detections while remaining backward-compatible with
-- historical data.
-- ============================================================


-- ============================================================
-- PATTERNS
-- ============================================================

ALTER TABLE patterns
    ADD COLUMN IF NOT EXISTS detection_key VARCHAR(64);

ALTER TABLE patterns
    ADD COLUMN IF NOT EXISTS detection_method VARCHAR(64);


-- Deterministic identity for newly persisted Pattern v1 rows.
--
-- detection_key is derived from stable evidence such as:
-- pattern_type + entity_id + ordered event_ids + method.
--
-- Existing legacy rows are intentionally excluded because
-- their detection_key is NULL.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_patterns_org_type_detection_key
ON patterns (
    organization_id,
    pattern_type,
    detection_key
)
WHERE detection_key IS NOT NULL;


-- Common Pattern API / worker read path:
-- recent patterns for one tenant.
CREATE INDEX IF NOT EXISTS
    ix_patterns_org_detected_at
ON patterns (
    organization_id,
    detected_at DESC
);


-- Recent Pattern rows by type.
CREATE INDEX IF NOT EXISTS
    ix_patterns_org_type_detected_at
ON patterns (
    organization_id,
    pattern_type,
    detected_at DESC
);


-- Active Pattern workspace view.
CREATE INDEX IF NOT EXISTS
    ix_patterns_org_active_detected_at
ON patterns (
    organization_id,
    is_active,
    detected_at DESC
);


-- ============================================================
-- ANOMALIES
-- ============================================================

ALTER TABLE anomalies
    ADD COLUMN IF NOT EXISTS detection_key VARCHAR(64);


-- Deterministic identity for newly persisted Anomaly v1 rows.
--
-- This intentionally uses a dedicated detection_key rather than
-- relying only on event_id + anomaly_type because future anomaly
-- detectors may use different baseline/window identities.
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_anomalies_org_type_detection_key
ON anomalies (
    organization_id,
    anomaly_type,
    detection_key
)
WHERE detection_key IS NOT NULL;


-- Fast event -> anomaly lookup.
CREATE INDEX IF NOT EXISTS
    ix_anomalies_event_id
ON anomalies (
    event_id
);


-- Recent anomaly timeline for one tenant.
CREATE INDEX IF NOT EXISTS
    ix_anomalies_org_detected_at
ON anomalies (
    organization_id,
    detected_at DESC
);


-- Recent anomalies by detector/type.
CREATE INDEX IF NOT EXISTS
    ix_anomalies_org_type_detected_at
ON anomalies (
    organization_id,
    anomaly_type,
    detected_at DESC
);


COMMIT;