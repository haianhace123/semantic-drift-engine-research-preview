BEGIN;

CREATE TABLE IF NOT EXISTS risk_assessments (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    semantic_drift_event_id UUID
        REFERENCES semantic_drift_events(id)
        ON DELETE SET NULL,

    risk_version VARCHAR(32) NOT NULL,

    assessed_at TIMESTAMPTZ NOT NULL,

    score DOUBLE PRECISION NOT NULL,

    semantic_magnitude_contribution DOUBLE PRECISION NOT NULL,
    legacy_impact_contribution DOUBLE PRECISION NOT NULL,
    semantic_severity_contribution DOUBLE PRECISION NOT NULL,
    page_context_contribution DOUBLE PRECISION NOT NULL,
    confidence_contribution DOUBLE PRECISION NOT NULL,

    topology_score DOUBLE PRECISION NOT NULL,
    topology_contribution DOUBLE PRECISION NOT NULL,

    topology_shift_component DOUBLE PRECISION NOT NULL,
    topology_centrality_component DOUBLE PRECISION NOT NULL,
    topology_downstream_component DOUBLE PRECISION NOT NULL,

    has_topology_signal BOOLEAN NOT NULL,
    has_previous_topology_state BOOLEAN NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_risk_assessment_score
        CHECK (
            score >= 0.0
            AND score <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_semantic_magnitude
        CHECK (
            semantic_magnitude_contribution >= 0.0
            AND semantic_magnitude_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_legacy_impact
        CHECK (
            legacy_impact_contribution >= 0.0
            AND legacy_impact_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_semantic_severity
        CHECK (
            semantic_severity_contribution >= 0.0
            AND semantic_severity_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_page_context
        CHECK (
            page_context_contribution >= 0.0
            AND page_context_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_confidence
        CHECK (
            confidence_contribution >= 0.0
            AND confidence_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_topology_score
        CHECK (
            topology_score >= 0.0
            AND topology_score <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_topology_contribution
        CHECK (
            topology_contribution >= 0.0
            AND topology_contribution <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_topology_shift
        CHECK (
            topology_shift_component >= 0.0
            AND topology_shift_component <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_topology_centrality
        CHECK (
            topology_centrality_component >= 0.0
            AND topology_centrality_component <= 1.0
        ),

    CONSTRAINT ck_risk_assessment_topology_downstream
        CHECK (
            topology_downstream_component >= 0.0
            AND topology_downstream_component <= 1.0
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_risk_assessment_event_version
ON risk_assessments (
    organization_id,
    semantic_drift_event_id,
    risk_version
)
WHERE semantic_drift_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_organization
ON risk_assessments (
    organization_id
);

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_event
ON risk_assessments (
    semantic_drift_event_id
);

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_org_assessed
ON risk_assessments (
    organization_id,
    assessed_at DESC
);

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_org_score
ON risk_assessments (
    organization_id,
    score DESC,
    assessed_at DESC
);

CREATE INDEX IF NOT EXISTS
    ix_risk_assessments_org_version
ON risk_assessments (
    organization_id,
    risk_version,
    assessed_at DESC
);

COMMIT;