BEGIN;

CREATE INDEX IF NOT EXISTS ix_lineage_dependencies_exact_revision
    ON lineage_observation_dependencies (
        asset_external_id,
        revision_external_id,
        observation_id
    );

COMMIT;