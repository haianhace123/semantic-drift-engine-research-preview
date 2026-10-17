BEGIN;

CREATE TABLE IF NOT EXISTS topology_shift_snapshots (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    semantic_drift_event_id UUID
        REFERENCES semantic_drift_events(id)
        ON DELETE SET NULL,

    previous_event_id UUID
        REFERENCES semantic_drift_events(id)
        ON DELETE SET NULL,

    observed_at TIMESTAMPTZ NOT NULL,

    previous_observed_at TIMESTAMPTZ,

    entity_count_delta INTEGER NOT NULL,

    active_edge_count_delta INTEGER NOT NULL,

    in_degree_delta INTEGER NOT NULL,

    out_degree_delta INTEGER NOT NULL,

    degree_delta INTEGER NOT NULL,

    degree_centrality_delta DOUBLE PRECISION NOT NULL,

    downstream_reach_delta INTEGER NOT NULL,

    downstream_reach_ratio_delta DOUBLE PRECISION NOT NULL,

    classification VARCHAR(32) NOT NULL,

    severity VARCHAR(16) NOT NULL,

    shift_score DOUBLE PRECISION NOT NULL,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    CONSTRAINT ck_topology_shift_score
        CHECK (
            shift_score >= 0.0
            AND shift_score <= 1.0
        ),

    CONSTRAINT ck_topology_shift_classification
        CHECK (
            classification IN (
                'stable',
                'expanded',
                'contracted',
                'centrality_gain',
                'centrality_loss',
                'downstream_gain',
                'downstream_loss'
            )
        ),

    CONSTRAINT ck_topology_shift_severity
        CHECK (
            severity IN (
                'none',
                'low',
                'medium',
                'high'
            )
        ),

    CONSTRAINT ck_topology_shift_previous_time
        CHECK (
            previous_observed_at IS NULL
            OR previous_observed_at < observed_at
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_topology_shift_snapshot_event_entity
ON topology_shift_snapshots (
    organization_id,
    entity_id,
    semantic_drift_event_id
)
WHERE semantic_drift_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_organization
ON topology_shift_snapshots (
    organization_id
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_entity
ON topology_shift_snapshots (
    entity_id
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_event
ON topology_shift_snapshots (
    semantic_drift_event_id
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_previous_event
ON topology_shift_snapshots (
    previous_event_id
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_org_observed
ON topology_shift_snapshots (
    organization_id,
    observed_at DESC
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_org_entity_observed
ON topology_shift_snapshots (
    organization_id,
    entity_id,
    observed_at DESC
);

CREATE INDEX IF NOT EXISTS
    ix_topology_shift_snapshots_org_severity_observed
ON topology_shift_snapshots (
    organization_id,
    severity,
    observed_at DESC
);

COMMIT;