-- Recovery 01.A2: insert-only evidence certificates, NOT serving permissions.
-- Issuance is restricted to an authorized tenant administrator by service.
-- Reading a row MUST NOT allow admission without request-time revalidation.
CREATE TABLE lineage_successor_recovery_certificates (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    source_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    snapshot_sha256 VARCHAR(64) NOT NULL,
    target_source_external_id VARCHAR(512) NOT NULL,
    target_asset_external_id VARCHAR(512) NOT NULL,
    target_revision_external_id VARCHAR(512) NOT NULL,
    chunk_revision_external_id VARCHAR(512) NOT NULL,
    chunk_content_sha256 VARCHAR(64) NOT NULL,
    chunk_observation_id UUID NOT NULL,
    index_observation_id UUID NOT NULL,
    binding_revision BIGINT NOT NULL,
    issued_by_user_id UUID NOT NULL REFERENCES users(id),
    evidence_kind VARCHAR(40) NOT NULL DEFAULT 'physical_successor_preflight_v1',
    grants_admission BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_successor_cert_snapshot_hash CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_successor_cert_chunk_hash CHECK (chunk_content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_successor_cert_binding_revision CHECK (binding_revision >= 1),
    CONSTRAINT ck_successor_cert_kind CHECK (evidence_kind = 'physical_successor_preflight_v1'),
    CONSTRAINT ck_successor_cert_not_admission CHECK (grants_admission IS FALSE),
    CONSTRAINT fk_successor_cert_chunk_observation FOREIGN KEY (organization_id,chunk_observation_id)
        REFERENCES lineage_observations(organization_id,id) ON DELETE RESTRICT,
    CONSTRAINT fk_successor_cert_index_observation FOREIGN KEY (organization_id,index_observation_id)
        REFERENCES lineage_observations(organization_id,id) ON DELETE RESTRICT,
    CONSTRAINT uq_successor_cert_exact_revision UNIQUE
        (organization_id,target_source_external_id,target_asset_external_id,target_revision_external_id)
);
CREATE INDEX ix_successor_cert_tenant_source_snapshot
    ON lineage_successor_recovery_certificates (organization_id,source_id,snapshot_id);

-- Deliberately no serving status projection and no HTTP admission change.
-- The service uses INSERT only; SQL user privileges/DDL triggers for append-only
-- enforcement are a separate deployment-security review requirement.
