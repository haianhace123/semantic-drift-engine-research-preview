BEGIN;

CREATE TABLE IF NOT EXISTS entity_graph_metric_snapshots (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    entity_id UUID NOT NULL
        REFERENCES entities(id)
        ON DELETE CASCADE,

    semantic_drift_event_id UUID NULL
        REFERENCES semantic_drift_events(id)
        ON DELETE SET NULL,

    observed_at TIMESTAMPTZ NOT NULL,

    entity_count INTEGER NOT NULL,
    active_edge_count INTEGER NOT NULL,

    in_degree INTEGER NOT NULL,
    out_degree INTEGER NOT NULL,
    degree INTEGER NOT NULL,

    in_degree_centrality DOUBLE PRECISION NOT NULL,
    out_degree_centrality DOUBLE PRECISION NOT NULL,
    degree_centrality DOUBLE PRECISION NOT NULL,

    downstream_reach INTEGER NOT NULL,
    downstream_reach_ratio DOUBLE PRECISION NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_entity_graph_metric_snapshot_entity_count
        CHECK (entity_count >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_active_edge_count
        CHECK (active_edge_count >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_in_degree
        CHECK (in_degree >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_out_degree
        CHECK (out_degree >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_degree
        CHECK (degree >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_degree_sum
        CHECK (degree = in_degree + out_degree),

    CONSTRAINT ck_entity_graph_metric_snapshot_in_centrality
        CHECK (
            in_degree_centrality >= 0.0
            AND in_degree_centrality <= 1.0
        ),

    CONSTRAINT ck_entity_graph_metric_snapshot_out_centrality
        CHECK (
            out_degree_centrality >= 0.0
            AND out_degree_centrality <= 1.0
        ),

    CONSTRAINT ck_entity_graph_metric_snapshot_degree_centrality
        CHECK (
            degree_centrality >= 0.0
            AND degree_centrality <= 1.0
        ),

    CONSTRAINT ck_entity_graph_metric_snapshot_downstream_reach
        CHECK (downstream_reach >= 0),

    CONSTRAINT ck_entity_graph_metric_snapshot_downstream_ratio
        CHECK (
            downstream_reach_ratio >= 0.0
            AND downstream_reach_ratio <= 1.0
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_entity_graph_metric_snapshot_event
ON entity_graph_metric_snapshots (
    organization_id,
    entity_id,
    semantic_drift_event_id
)
WHERE semantic_drift_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    ix_entity_graph_metric_snapshots_organization_id
ON entity_graph_metric_snapshots (
    organization_id
);

CREATE INDEX IF NOT EXISTS
    ix_entity_graph_metric_snapshots_entity_id
ON entity_graph_metric_snapshots (
    entity_id
);

CREATE INDEX IF NOT EXISTS
    ix_entity_graph_metric_snapshots_event_id
ON entity_graph_metric_snapshots (
    semantic_drift_event_id
);

CREATE INDEX IF NOT EXISTS
    ix_entity_graph_metric_snapshots_org_observed_at
ON entity_graph_metric_snapshots (
    organization_id,
    observed_at DESC
);

CREATE INDEX IF NOT EXISTS
    ix_entity_graph_metric_snapshots_org_entity_observed_at
ON entity_graph_metric_snapshots (
    organization_id,
    entity_id,
    observed_at DESC
);

COMMIT;