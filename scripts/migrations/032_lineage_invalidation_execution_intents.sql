BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION EXECUTION INTENTS
--
-- Durable authorization facts permitting execution of an exact
-- persisted lineage invalidation decision.
--
-- A decision remains policy evidence only. An execution intent
-- records that the decision passed operational authority checks
-- while the relevant policy and target authorities were locked.
--
-- At most one execution intent exists for one immutable decision
-- within an organization. Execution attempts, retries, delivery
-- state, and reconciliation state deliberately do not belong here.
--
-- authorized_at is audit metadata. It is not concurrency or
-- ordering authority.
-- ============================================================

ALTER TABLE lineage_invalidation_decisions
    ADD CONSTRAINT uq_lineage_invalidation_decisions_org_id
    UNIQUE (
        organization_id,
        id
    );


CREATE TABLE IF NOT EXISTS lineage_invalidation_execution_intents (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,
    decision_id UUID NOT NULL,

    authorized_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_invalidation_execution_intents_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT lineage_invalidation_execution_intents_decision_fkey
        FOREIGN KEY (
            organization_id,
            decision_id
        )
        REFERENCES lineage_invalidation_decisions (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT uq_lineage_invalidation_execution_intent_decision
        UNIQUE (
            organization_id,
            decision_id
        )
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_execution_intents_org_authorized_id
ON lineage_invalidation_execution_intents (
    organization_id,
    authorized_at ASC,
    id ASC
);


COMMENT ON TABLE lineage_invalidation_execution_intents IS
    'Durable organization-scoped authorization facts permitting execution of exact persisted lineage invalidation decisions.';

COMMENT ON COLUMN lineage_invalidation_execution_intents.id IS
    'Server-generated storage identity. It is not logical execution intent identity.';

COMMENT ON COLUMN lineage_invalidation_execution_intents.decision_id IS
    'Exact persisted invalidation decision authorized for execution.';

COMMENT ON COLUMN lineage_invalidation_execution_intents.authorized_at IS
    'Time when execution authority was durably established. Audit metadata only, not concurrency authority.';

COMMIT;
