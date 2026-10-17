BEGIN;

CREATE TABLE lineage_execution_target_bindings (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    source_external_id VARCHAR NOT NULL,
    asset_external_id VARCHAR NOT NULL,
    binding_revision BIGINT NOT NULL,
    provider VARCHAR NOT NULL,
    target_kind VARCHAR NOT NULL,
    configuration JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_lineage_execution_target_bindings_organization
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_execution_target_bindings_positive_revision
        CHECK (binding_revision > 0),

    CONSTRAINT uq_lineage_execution_target_binding_revision
        UNIQUE (
            organization_id,
            source_external_id,
            asset_external_id,
            binding_revision
        )
);

CREATE INDEX ix_lineage_execution_target_bindings_org_target_revision
    ON lineage_execution_target_bindings (
        organization_id,
        source_external_id,
        asset_external_id,
        binding_revision
    );

COMMIT;