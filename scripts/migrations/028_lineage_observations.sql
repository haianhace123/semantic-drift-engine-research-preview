BEGIN;

-- ============================================================
-- CAUSAL LINEAGE OBSERVATIONS
--
-- Durable, organization-scoped observations of exact knowledge
-- materialization and consumption.
--
-- The database UUID is storage identity only. Immutable causal
-- observation identity is:
--
--     organization
--     + source
--     + asset
--     + observation kind
--     + sequence
--
-- Dependencies preserve exact upstream asset and revision
-- identities. They deliberately do not reference the entity graph
-- or infer a current/latest revision.
-- ============================================================

CREATE TABLE IF NOT EXISTS lineage_observations (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,

    observation_kind VARCHAR(32) NOT NULL,

    source_external_id VARCHAR(512) NOT NULL,
    snapshot_external_id VARCHAR(512) NOT NULL,

    asset_external_id VARCHAR(512) NOT NULL,
    asset_kind VARCHAR(32) NOT NULL,
    revision_external_id VARCHAR(512) NOT NULL,

    sequence BIGINT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_observations_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_observations_kind
        CHECK (
            observation_kind IN (
                'materialization',
                'consumption'
            )
        ),

    CONSTRAINT ck_lineage_observations_asset_kind
        CHECK (
            asset_kind IN (
                'chunk',
                'index',
                'agent',
                'workflow'
            )
        ),

    CONSTRAINT ck_lineage_observations_sequence_nonnegative
        CHECK (sequence >= 0),

    CONSTRAINT uq_lineage_observation_identity
        UNIQUE (
            organization_id,
            source_external_id,
            asset_external_id,
            observation_kind,
            sequence
        )
);

CREATE TABLE IF NOT EXISTS lineage_observation_dependencies (
    id UUID PRIMARY KEY,

    observation_id UUID NOT NULL,

    asset_external_id VARCHAR(512) NOT NULL,
    revision_external_id VARCHAR(512) NOT NULL,
    dependency_type VARCHAR(128) NOT NULL,

    CONSTRAINT lineage_observation_dependencies_observation_id_fkey
        FOREIGN KEY (observation_id)
        REFERENCES lineage_observations(id)
        ON DELETE CASCADE,

    CONSTRAINT uq_lineage_observation_dependency
        UNIQUE (
            observation_id,
            asset_external_id,
            revision_external_id,
            dependency_type
        )
);

CREATE INDEX IF NOT EXISTS
    ix_lineage_observations_org_created_id
ON lineage_observations (
    organization_id,
    created_at ASC,
    id ASC
);

CREATE INDEX IF NOT EXISTS
    ix_lineage_observations_org_subject_sequence
ON lineage_observations (
    organization_id,
    source_external_id,
    asset_external_id,
    observation_kind,
    sequence DESC
);

CREATE INDEX IF NOT EXISTS
    ix_lineage_dependencies_observation_id
ON lineage_observation_dependencies (
    observation_id
);

COMMENT ON TABLE lineage_observations IS
    'Organization-scoped durable causal lineage observations for exact knowledge materialization and consumption.';

COMMENT ON TABLE lineage_observation_dependencies IS
    'Exact revision dependencies attached atomically to durable lineage observations.';

COMMENT ON COLUMN lineage_observations.id IS
    'Server-generated storage identity. It is not causal asset or revision identity.';

COMMENT ON COLUMN lineage_observations.sequence IS
    'Producer observation sequence scoped by organization, source, asset, and observation kind.';

COMMENT ON COLUMN lineage_observation_dependencies.dependency_type IS
    'Open relationship vocabulary supplied by the lineage producer or adapter.';

COMMIT;