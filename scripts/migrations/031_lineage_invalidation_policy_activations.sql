BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION POLICY ACTIVATIONS
--
-- Durable organization-scoped authority selecting the exact
-- invalidation policy revision that is operationally active.
--
-- One row exists per:
--
--     organization
--     + policy external identity
--
-- active_revision advances monotonically. Replaying the same
-- revision is idempotent. A lower revision must never replace a
-- higher active revision.
--
-- activated_at records when the currently active revision was
-- selected. It is audit metadata, not version or concurrency
-- authority.
--
-- Policy definition, decision facts, execution state, and
-- reconciliation state deliberately do not belong here.
-- ============================================================

CREATE TABLE IF NOT EXISTS lineage_invalidation_policy_activations (
    organization_id UUID NOT NULL,
    policy_external_id VARCHAR(512) NOT NULL,
    active_revision BIGINT NOT NULL,

    activated_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_invalidation_policy_activations_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_invalidation_policy_activation_revision_nonnegative
        CHECK (active_revision >= 0),

    CONSTRAINT pk_lineage_invalidation_policy_activations
        PRIMARY KEY (
            organization_id,
            policy_external_id
        )
);


COMMENT ON TABLE lineage_invalidation_policy_activations IS
    'Organization-scoped authority selecting the exact operationally active lineage invalidation policy revision.';

COMMENT ON COLUMN lineage_invalidation_policy_activations.policy_external_id IS
    'Stable external identity of the invalidation policy whose active revision is controlled by this row.';

COMMENT ON COLUMN lineage_invalidation_policy_activations.active_revision IS
    'Exact operationally active policy revision. Revision advancement is monotonic.';

COMMENT ON COLUMN lineage_invalidation_policy_activations.activated_at IS
    'Time when the currently active revision was selected. Audit metadata only, not concurrency authority.';

COMMIT;