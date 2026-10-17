BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION DECISIONS
--
-- Durable, organization-scoped policy decisions derived from
-- exact causal lineage impact.
--
-- The database UUID is storage identity only. Immutable decision
-- identity is:
--
--     organization
--     + exact root revision
--     + exact target revision
--     + invalidation policy identity and revision
--
-- Disposition, reason code, and ordered causal path are immutable
-- decision payload. Execution and reconciliation state deliberately
-- do not belong to this contract.
-- ============================================================

CREATE TABLE IF NOT EXISTS lineage_invalidation_decisions (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,

    root_source_external_id VARCHAR(512) NOT NULL,
    root_asset_external_id VARCHAR(512) NOT NULL,
    root_revision_external_id VARCHAR(512) NOT NULL,

    target_source_external_id VARCHAR(512) NOT NULL,
    target_asset_external_id VARCHAR(512) NOT NULL,
    target_revision_external_id VARCHAR(512) NOT NULL,

    policy_external_id VARCHAR(512) NOT NULL,
    policy_revision BIGINT NOT NULL,

    disposition VARCHAR(32) NOT NULL,
    reason_code VARCHAR(128) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_invalidation_decisions_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_invalidation_decisions_policy_revision_nonnegative
        CHECK (policy_revision >= 0),

    CONSTRAINT ck_lineage_invalidation_decisions_disposition
        CHECK (
            disposition IN (
                'no_action',
                'invalidate',
                'rematerialize'
            )
        ),

    CONSTRAINT uq_lineage_invalidation_decision_identity
        UNIQUE (
            organization_id,
            root_source_external_id,
            root_asset_external_id,
            root_revision_external_id,
            target_source_external_id,
            target_asset_external_id,
            target_revision_external_id,
            policy_external_id,
            policy_revision
        )
);


CREATE TABLE IF NOT EXISTS lineage_invalidation_decision_path_revisions (
    id UUID PRIMARY KEY,

    decision_id UUID NOT NULL,

    ordinal INTEGER NOT NULL,

    source_external_id VARCHAR(512) NOT NULL,
    asset_external_id VARCHAR(512) NOT NULL,
    revision_external_id VARCHAR(512) NOT NULL,

    CONSTRAINT lineage_invalidation_decision_path_revisions_decision_id_fkey
        FOREIGN KEY (decision_id)
        REFERENCES lineage_invalidation_decisions(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_invalidation_path_ordinal_nonnegative
        CHECK (ordinal >= 0),

    CONSTRAINT uq_lineage_invalidation_path_ordinal
        UNIQUE (
            decision_id,
            ordinal
        )
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_decisions_org_created_id
ON lineage_invalidation_decisions (
    organization_id,
    created_at ASC,
    id ASC
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_decisions_org_target
ON lineage_invalidation_decisions (
    organization_id,
    target_source_external_id,
    target_asset_external_id,
    target_revision_external_id
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_path_decision_ordinal
ON lineage_invalidation_decision_path_revisions (
    decision_id,
    ordinal
);


COMMENT ON TABLE lineage_invalidation_decisions IS
    'Organization-scoped durable invalidation decisions derived from exact causal lineage impact and a pinned policy revision.';

COMMENT ON TABLE lineage_invalidation_decision_path_revisions IS
    'Ordered exact revision path providing causal evidence for a durable lineage invalidation decision.';

COMMENT ON COLUMN lineage_invalidation_decisions.id IS
    'Server-generated storage identity. It is not logical decision identity.';

COMMENT ON COLUMN lineage_invalidation_decisions.disposition IS
    'Policy decision only. It does not represent execution or reconciliation state.';

COMMENT ON COLUMN lineage_invalidation_decisions.policy_revision IS
    'Pinned revision of the invalidation policy used to derive the immutable decision.';

COMMENT ON COLUMN lineage_invalidation_decision_path_revisions.ordinal IS
    'Zero-based position of an exact revision in the ordered causal path.';

COMMIT;