BEGIN;

CREATE TABLE temporal_lineage_correlations (
    page_snapshot_id UUID NOT NULL,
    lineage_observation_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_temporal_lineage_correlations
        PRIMARY KEY (
            page_snapshot_id,
            lineage_observation_id
        ),

    CONSTRAINT fk_temporal_lineage_correlations_snapshot
        FOREIGN KEY (page_snapshot_id)
        REFERENCES page_snapshots(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_temporal_lineage_correlations_observation
        FOREIGN KEY (lineage_observation_id)
        REFERENCES lineage_observations(id)
        ON DELETE CASCADE
);

CREATE INDEX ix_temporal_lineage_correlations_observation_snapshot
ON temporal_lineage_correlations (
    lineage_observation_id,
    page_snapshot_id
);

COMMENT ON TABLE temporal_lineage_correlations IS
'Exact evidence linking a temporal page snapshot to a persisted lineage observation. Absence of a row means lineage correlation is unavailable, not that downstream impact is empty.';

COMMENT ON COLUMN temporal_lineage_correlations.page_snapshot_id IS
'Internal temporal snapshot whose observed content is correlated with lineage evidence.';

COMMENT ON COLUMN temporal_lineage_correlations.lineage_observation_id IS
'Persisted lineage observation providing the canonical external revision identity.';

COMMIT;