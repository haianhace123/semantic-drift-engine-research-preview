BEGIN;

-- ============================================================
-- LINEAGE INVALIDATION EXECUTION RESULTS
--
-- Immutable completion facts for exact lineage invalidation
-- execution attempts.
--
-- An execution attempt is the bounded fencing authority for
-- external work. A result records the terminal outcome reported
-- by that exact attempt while it still held execution authority.
--
-- At most one result exists for one organization-scoped attempt.
-- The attempt UUID identifies the exact fencing authority that
-- produced the result.
--
-- Results do not define retry policy, scheduling, external
-- idempotency, reconciliation state, or mutable worker status.
--
-- recorded_at is audit metadata. It is not execution or fencing
-- authority.
-- ============================================================

ALTER TABLE lineage_invalidation_execution_attempts
    ADD CONSTRAINT uq_lineage_invalidation_execution_attempts_org_id
    UNIQUE (
        organization_id,
        id
    );


CREATE TABLE IF NOT EXISTS lineage_invalidation_execution_results (
    id UUID PRIMARY KEY,

    organization_id UUID NOT NULL,
    attempt_id UUID NOT NULL,

    outcome VARCHAR(16) NOT NULL,

    recorded_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT lineage_invalidation_execution_results_organization_id_fkey
        FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    CONSTRAINT lineage_invalidation_execution_results_attempt_fkey
        FOREIGN KEY (
            organization_id,
            attempt_id
        )
        REFERENCES lineage_invalidation_execution_attempts (
            organization_id,
            id
        )
        ON DELETE CASCADE,

    CONSTRAINT ck_lineage_invalidation_execution_result_outcome
        CHECK (
            outcome IN (
                'succeeded',
                'failed'
            )
        ),

    CONSTRAINT uq_lineage_invalidation_execution_result_attempt
        UNIQUE (
            organization_id,
            attempt_id
        )
);


CREATE INDEX IF NOT EXISTS
    ix_lineage_invalidation_execution_results_org_recorded_id
ON lineage_invalidation_execution_results (
    organization_id,
    recorded_at ASC,
    id ASC
);


COMMENT ON TABLE lineage_invalidation_execution_results IS
    'Immutable organization-scoped terminal outcomes for exact lineage invalidation execution attempts.';

COMMENT ON COLUMN lineage_invalidation_execution_results.id IS
    'Server-generated storage identity.';

COMMENT ON COLUMN lineage_invalidation_execution_results.attempt_id IS
    'Exact execution attempt and fencing identity that produced this result.';

COMMENT ON COLUMN lineage_invalidation_execution_results.outcome IS
    'Terminal execution outcome: succeeded or failed.';

COMMENT ON COLUMN lineage_invalidation_execution_results.recorded_at IS
    'Time when the execution result was durably recorded. Audit metadata only, not fencing authority.';

COMMIT;