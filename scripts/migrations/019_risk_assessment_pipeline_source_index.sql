BEGIN;

-- ============================================================
-- RISK ASSESSMENT PIPELINE SOURCE INDEX
--
-- Supports organization-level downstream intelligence discovery:
--
--   RiskAssessment
--       -> Pattern Detection
--       -> Anomaly Detection
--
-- Downstream pipeline checkpoints use the deterministic Risk v2
-- materialization watermark:
--
--   (created_at, id)
--
-- scoped by:
--
--   organization_id
--   risk_version
--
-- The id column is the stable tie-breaker for rows that share the
-- same created_at timestamp.
-- ============================================================

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_org_version_created_id
ON risk_assessments (
    organization_id,
    risk_version,
    created_at DESC,
    id DESC
);

COMMIT;