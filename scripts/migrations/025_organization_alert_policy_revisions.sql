BEGIN;

-- ============================================================
-- ORGANIZATION ALERT POLICY REVISION
--
-- Watchlist configuration is mutable policy state. Alert
-- reconciliation must not infer change ordering from mutable
-- timestamps or UUID ordering.
--
-- policy_revision:
--     Monotonic organization-scoped revision incremented in the
--     same transaction as every Watchlist mutation that can change
--     Alert candidate policy.
--
-- reconciled_revision:
--     Highest policy revision successfully reconciled by Alert
--     generation. It advances only after reconciliation work has
--     completed successfully in the caller-owned transaction.
--
-- A single row per organization makes revision increments atomic
-- and provides a row-level serialization point for reconciliation.
-- ============================================================

CREATE TABLE IF NOT EXISTS organization_alert_policy_revisions (
    organization_id UUID PRIMARY KEY,

    policy_revision BIGINT NOT NULL
        DEFAULT 0,

    reconciled_revision BIGINT NOT NULL
        DEFAULT 0,

    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    reconciled_at TIMESTAMPTZ,

    CONSTRAINT organization_alert_policy_revisions_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT ck_alert_policy_revision_nonnegative
        CHECK (policy_revision >= 0),

    CONSTRAINT ck_alert_policy_reconciled_revision_nonnegative
        CHECK (reconciled_revision >= 0),

    CONSTRAINT ck_alert_policy_reconciled_lte_policy
        CHECK (reconciled_revision <= policy_revision)
);

COMMENT ON TABLE organization_alert_policy_revisions IS
    'Organization-scoped monotonic revision state for durable Alert policy reconciliation.';

COMMENT ON COLUMN organization_alert_policy_revisions.policy_revision IS
    'Current monotonic Alert policy revision. Incremented transactionally with policy mutations.';

COMMENT ON COLUMN organization_alert_policy_revisions.reconciled_revision IS
    'Highest Alert policy revision successfully reconciled by Alert generation.';

COMMENT ON COLUMN organization_alert_policy_revisions.updated_at IS
    'Time when the current policy revision was most recently incremented.';

COMMENT ON COLUMN organization_alert_policy_revisions.reconciled_at IS
    'Time when reconciled_revision most recently advanced.';

COMMIT;
